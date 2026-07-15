"""Container & Deployment Hardening Standard v1 + the auxiliary sidecar role contract.

The central gate (genefoundry-router `validate-compose`) is the authority: it renders
base + prod and rejects any unhardened service. These tests guard the invariants a render
alone cannot show -- that the *base* file is safe by default, that the declared sidecar
role in `container-release.json` matches the compose it authorizes, and that the reviewed
data identity has exactly one source of truth.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "docker"


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that understands the Compose-spec merge tags `!reset` / `!override`.

    Overlay files use them to clear or replace inherited base values instead of merging
    them. They are not standard YAML tags, so plain `yaml.safe_load` raises
    `ConstructorError`; we only need the underlying value for these assertions.
    """


def _construct_tagged(loader: _ComposeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!reset", _construct_tagged)
_ComposeLoader.add_constructor("!override", _construct_tagged)


def _compose(compose_file: str) -> dict[str, Any]:
    text = (DOCKER / compose_file).read_text(encoding="utf-8")
    return yaml.load(text, Loader=_ComposeLoader)  # noqa: S506 - subclasses SafeLoader


def _service(compose_file: str, service: str) -> dict[str, Any]:
    return _compose(compose_file)["services"][service]


def _release_config() -> dict[str, Any]:
    return json.loads((ROOT / "container-release.json").read_text(encoding="utf-8"))


# --- the application service -----------------------------------------------------------


def test_base_compose_is_hardened() -> None:
    svc = _service("docker-compose.yml", "clingen-link")
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["init"] is True
    assert svc["deploy"]["resources"]["limits"]["pids"]
    assert svc["deploy"]["resources"]["limits"]["memory"]
    # The service-level `pids_limit` key is rejected by the central policy: resource
    # limits belong under `deploy.resources.limits` only.
    assert "pids_limit" not in svc


def test_base_compose_binds_loopback_only() -> None:
    svc = _service("docker-compose.yml", "clingen-link")
    # An auth=none backend must never be published on 0.0.0.0.
    assert svc["ports"], "base compose must still publish a loopback port for the quick-start"
    assert all(str(p).startswith("127.0.0.1:") for p in svc["ports"]), svc["ports"]


def test_application_mounts_the_reference_volume_read_only() -> None:
    svc = _service("docker-compose.yml", "clingen-link")
    mounts = {mount["target"]: mount for mount in svc["volumes"]}
    assert mounts["/data"]["type"] == "volume"
    assert mounts["/data"]["read_only"] is True, "the selected snapshot is authoritative"


# --- the clingen-data-init sidecar -----------------------------------------------------


def test_init_sidecar_is_declared_with_its_role() -> None:
    auxiliary = _release_config()["service"]["auxiliary"]
    assert [entry["name"] for entry in auxiliary] == ["clingen-data-init"]
    rule = auxiliary[0]
    assert rule["role"] == "init"
    # It reads an already-verified artifact from disk: it needs no network at all.
    assert rule["egress"] == "denied"
    assert set(rule["writable_targets"]) == {"/data", "/tmp"}  # noqa: S108 - mount target
    assert set(rule["read_only_targets"]) == {"/seed"}


def test_init_sidecar_compose_matches_its_declared_role() -> None:
    svc = _service("docker-compose.yml", "clingen-data-init")
    assert svc["network_mode"] == "none", "egress-denied role"
    assert svc["restart"] == "no", "a one-shot init sidecar must not restart"
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert isinstance(svc["command"], list), "explicit shell-free argv"
    assert "healthcheck" not in svc and "expose" not in svc, "an init sidecar serves no traffic"

    mounts = {mount["target"]: mount for mount in svc["volumes"]}
    assert mounts["/seed"]["type"] == "bind"
    assert mounts["/seed"]["read_only"] is True, "the seed artifact is never writable"
    assert mounts["/data"]["type"] == "volume"
    assert not mounts["/data"].get("read_only"), "the init sidecar selects into /data"


def test_application_waits_for_the_init_sidecar() -> None:
    svc = _service("docker-compose.yml", "clingen-link")
    assert svc["depends_on"]["clingen-data-init"]["condition"] == "service_completed_successfully"


# --- one source of truth for the reviewed data identity --------------------------------


def test_every_compose_schema_pin_matches_the_data_contract() -> None:
    """Every deploy declares the schema version the ETL actually stamps — no exceptions.

    The v4 dosage fix (issue #46) bumped the snapshot schema to 2. Missing even ONE pin here
    is a CRITICAL: a deploy declaring the old schema either serves the prose-in-score bundle
    (silent-empty codes 30/40 again) or fails materialization on a v2 bundle. The value is
    DERIVED from `data_contract.SNAPSHOT_SCHEMA_SEMVER`, not hardcoded, and the file list is a
    GLOB — a new compose overlay is covered the day it lands, not when someone remembers to add
    it here. Prove this guard by breaking it: change the contract and watch every pin fail.
    """
    from clingen_link.data_contract import SNAPSHOT_SCHEMA_SEMVER

    pin = re.compile(r"CLINGEN_LINK_DATA_SCHEMA_(?:VERSION|MINIMUM|MAXIMUM):-([0-9][^}\"]*)")
    found = 0
    for path in sorted(DOCKER.glob("docker-compose*.yml")):
        for declared in pin.findall(path.read_text(encoding="utf-8")):
            found += 1
            assert declared == SNAPSHOT_SCHEMA_SEMVER, (
                f"{path.name} pins schema {declared!r}, but the ETL stamps "
                f"{SNAPSHOT_SCHEMA_SEMVER!r} (clingen_link.data_contract)"
            )
    assert found >= 6, f"expected the base + npm schema pins, found {found}"


def test_compose_data_digest_matches_the_declared_data_release() -> None:
    """The compose default and container-release.json must name the SAME artifact.

    They are read by different actors (an operator running `docker compose up`, and the
    central release gate). If they drift, the stack silently verifies a bundle other than
    the one the release contract pins.
    """
    declared = _release_config()["data"]["digest"].removeprefix("sha256:")
    base = (DOCKER / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"CLINGEN_LINK_DATA_BUNDLE_SHA256:-{declared}" in base
    npm = (DOCKER / "docker-compose.npm.yml").read_text(encoding="utf-8")
    assert f"CLINGEN_LINK_DATA_BUNDLE_SHA256:-{declared}" in npm
    env_example = (ROOT / ".env.docker.example").read_text(encoding="utf-8")
    assert f"CLINGEN_LINK_DATA_BUNDLE_SHA256={declared}" in env_example


def test_smoke_preparation_hook_verifies_the_committed_digest() -> None:
    config = _release_config()
    assert config["preparation"] == "docker/ci-prepare-smoke.sh"
    hook = ROOT / "docker" / "ci-prepare-smoke.sh"
    assert hook.is_file()
    # The sidecar has no egress, so this hook is the only place data is fetched -- and it
    # must prove the bytes against the committed digest before the stack ever sees them.
    body = hook.read_text(encoding="utf-8")
    assert "sha256sum -c -" in body
    assert ".data.digest" in body


# --- the self-contained npm overlay ----------------------------------------------------


def test_npm_overlay_is_self_contained_with_writable_tmpfs() -> None:
    # The npm overlay is deployed as a SINGLE, self-contained compose file (the
    # GeneFoundry -link fleet standard) under its own service key (clingen_link). It is
    # NOT layered over docker-compose.yml, so it must declare its own writable /tmp
    # tmpfs -- the image sets TMPDIR=/tmp and a read_only rootfs has no other scratch.
    svc = _service("docker-compose.npm.yml", "clingen_link")
    assert svc["read_only"] is True
    tmpfs = svc.get("tmpfs", [])
    # S108 is a false positive: this asserts a compose mount target, not a runtime path.
    assert any(str(entry).startswith("/tmp:") for entry in tmpfs), tmpfs  # noqa: S108
    assert svc["cap_drop"] == ["ALL"]
    assert "pids_limit" not in svc


def test_npm_overlay_materializes_its_own_reference_data() -> None:
    npm = _compose("docker-compose.npm.yml")
    assert "clingen_data_init" in npm["services"], "the npm path is code-only too"
    server = npm["services"]["clingen_link"]
    condition = server["depends_on"]["clingen_data_init"]["condition"]
    assert condition == "service_completed_successfully"
