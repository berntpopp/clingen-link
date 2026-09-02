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
DATA_RELEASE_TAG = "data-clingen-de5f403028d2e1e1"
DATA_IDENTITY_DIGEST = "sha256:74dc6e1a82f773b17303d33ff82b63c96e9aed0b16fa5f3020b13fd69ffdf789"
RUNTIME_DATA_IDENTITY_DIGEST = (
    "sha256:74dc6e1a82f773b17303d33ff82b63c96e9aed0b16fa5f3020b13fd69ffdf789"
)
# genefoundry-router v0.8.5: the first revision whose `_container-release.yml` runs
# `validate-deployed-overlay` against docker/docker-compose.npm.yml.
ROUTER_WORKFLOW_SHA = "31ea81cee5475fc3655c047c63a89739948f99a9"
RUNTIME_CAPABLE_RELEASE_BUILDER = (
    f"berntpopp/genefoundry-router/.github/workflows/_container-release.yml@{ROUTER_WORKFLOW_SHA}"
)
RUNTIME_CAPABLE_CI_BUILDER = (
    f"berntpopp/genefoundry-router/.github/workflows/_container-ci.yml@{ROUTER_WORKFLOW_SHA}"
)
# The physical Docker volume the deployed stack uses today. Compose derives it from the
# project name plus the logical key, so the default below must stay byte-identical.
CURRENT_REFERENCE_VOLUME = "clingen-link-npm_clingen_reference"


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


def test_every_compose_profile_carries_the_declared_runtime_data_identity() -> None:
    """The compose default and container-release.json must name the SAME artifact.

    They are read by different actors (an operator running `docker compose up`, and the
    central release gate). If they drift, the stack silently verifies a bundle other than
    the one the release contract pins.
    """
    declared = _release_config()["data"]
    assert declared["digest"] == RUNTIME_DATA_IDENTITY_DIGEST
    assert declared == {
        "mode": "external-reference",
        "release_tag": DATA_RELEASE_TAG,
        "digest": DATA_IDENTITY_DIGEST,
        "image_allowlist": [
            "opt/venv/lib/python3.14/site-packages/clingen_link/data/svi_guidance.json"
        ],
    }
    for compose_name in (
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "docker-compose.npm.yml",
    ):
        text = (DOCKER / compose_name).read_text(encoding="utf-8")
        assert (
            f'CLINGEN_LINK_DATA_RELEASE_TAG: "${{CLINGEN_LINK_DATA_RELEASE_TAG:-{DATA_RELEASE_TAG}}}"'
            in text
        )
        assert (
            f'CLINGEN_LINK_DATA_IDENTITY_DIGEST: "${{CLINGEN_LINK_DATA_IDENTITY_DIGEST:-{DATA_IDENTITY_DIGEST}}}"'
            in text
        )
    env_example = (ROOT / ".env.docker.example").read_text(encoding="utf-8")
    assert f"CLINGEN_LINK_DATA_RELEASE_TAG={DATA_RELEASE_TAG}" in env_example
    assert f"CLINGEN_LINK_DATA_IDENTITY_DIGEST={DATA_IDENTITY_DIGEST}" in env_example


def test_declared_data_release_is_compatible_with_the_application_schema() -> None:
    """A schema-2 application must not publish a schema-1 data requirement."""
    from clingen_link.data_contract import SNAPSHOT_SCHEMA_SEMVER, SNAPSHOT_SCHEMA_VERSION

    declared = _release_config()["data"]
    assert declared["release_tag"] == DATA_RELEASE_TAG
    assert declared["digest"] == DATA_IDENTITY_DIGEST
    # `data.schema_compatibility` is what the release workflow projects into the published
    # manifest's `data_requirements.schema_compatibility`, and the fleet controller refuses
    # a data-activation record whose attested `schema_version` is not a member of it
    # (strato_v6_docker_npm `scripts/utils/data_attestation.py`). It cannot be declared yet:
    # the router's own `ReleaseConfig` data models are `extra="forbid"` and carry no such
    # field, so adding it here makes `validate-deployed-overlay` fail the release outright.
    # When the router grows the field, the value is the RAW `meta.snapshot_version` stamp
    # (`SNAPSHOT_SCHEMA_VERSION`, "2") that the controller's probe reads back out of the
    # SQLite file -- not the deployment pin `SNAPSHOT_SCHEMA_SEMVER` ("2.0.0").
    assert SNAPSHOT_SCHEMA_VERSION == "2"
    assert SNAPSHOT_SCHEMA_SEMVER == "2.0.0"
    assert "schema_compatibility" not in declared


def test_smoke_preparation_hook_verifies_the_committed_digest() -> None:
    config = _release_config()
    assert config["preparation"] == "docker/ci-prepare-smoke.sh"
    hook = ROOT / "docker" / "ci-prepare-smoke.sh"
    assert hook.is_file()
    # The sidecar has no egress, so this hook is the only place data is fetched -- and it
    # must prove the bytes against the committed digest before the stack ever sees them.
    body = hook.read_text(encoding="utf-8")
    assert "sha256sum -c -" in body
    assert "CLINGEN_LINK_DATA_BUNDLE_SHA256" in body
    assert "CLINGEN_LINK_DATA_RELEASE_TAG" in body
    assert "CLINGEN_LINK_DATA_IDENTITY_DIGEST" in body


def test_container_release_opts_into_runtime_v1_with_schema_compatible_shape() -> None:
    config = _release_config()
    assert config["schema_version"] == 1
    assert config["definitions"] == {"contract": "data-bound"}
    assert config["data_identity_contract"] == "runtime-v1"
    assert config["data"]["mode"] == "external-reference"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", config["data"]["digest"])


def test_runtime_v1_release_pins_the_reviewed_runtime_capable_builder() -> None:
    """A runtime-v1 declaration is safe only with the reviewed runtime-aware builder."""
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "container-release.yml").read_text(encoding="utf-8")
    )

    assert _release_config()["data_identity_contract"] == "runtime-v1"
    assert workflow["jobs"]["release"]["uses"] == RUNTIME_CAPABLE_RELEASE_BUILDER
    assert workflow["permissions"] == {
        "attestations": "write",
        "contents": "write",
        "id-token": "write",
        "packages": "write",
    }


def test_runtime_v1_ci_pins_the_reviewed_runtime_capable_builder() -> None:
    """PR validation must understand the same runtime-v1 release configuration."""
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "container-ci.yml").read_text(encoding="utf-8")
    )

    assert _release_config()["data_identity_contract"] == "runtime-v1"
    assert workflow["jobs"]["container-ci"]["uses"] == RUNTIME_CAPABLE_CI_BUILDER


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


# --- fleet deploy contract: numeric non-root user ---------------------------------------

NUMERIC_USER = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")


def test_npm_overlay_declares_numeric_user_for_every_service() -> None:
    """The deployed NPM overlay wants a numeric non-root `user` on every service; the
    release Compose files feed a shared gate that forbids that same key entirely."""
    npm = _compose("docker-compose.npm.yml")
    for name, service in npm["services"].items():
        user = service.get("user")
        assert user is not None, f"{name} must declare a numeric user in the NPM overlay"
        assert NUMERIC_USER.match(str(user)), (
            f"{name} declares user={user!r}; the deploy contract requires "
            "'<uid>:<gid>' with both non-root and numeric"
        )


def test_release_compose_files_never_declare_user() -> None:
    for rel_path in _release_config()["service"]["compose_files"]:
        compose = _compose(Path(rel_path).name)
        for name, service in compose["services"].items():
            assert "user" not in service, (
                f"{name} in {rel_path} declares 'user'; the release Compose gate "
                "(container_release.py validate-compose) forbids it there"
            )


# --- fleet deploy contract: activation-ready overlay -------------------------------------


def test_npm_overlay_reference_volume_is_selectable_and_defaults_to_the_live_volume() -> None:
    """The controller prepares a *candidate* volume, verifies it, then switches to it.

    That is only possible if the physical volume name is a variable. The default must stay
    the name Compose derives on its own (`<project>_<logical key>`), so an operator who sets
    nothing keeps the exact volume the service already serves from.
    """
    npm = _compose("docker-compose.npm.yml")
    volume = npm["volumes"]["clingen_reference"]
    assert volume["name"] == f"${{CLINGEN_REFERENCE_VOLUME:-{CURRENT_REFERENCE_VOLUME}}}"
    assert f"{npm['name']}_clingen_reference" == CURRENT_REFERENCE_VOLUME
    for service in npm["services"].values():
        sources = {mount.get("source") for mount in service.get("volumes", [])}
        assert "clingen_reference" in sources or sources == {
            "${CLINGEN_LINK_DATA_SEED_DIR:-/srv/genefoundry/clingen-seed}"
        }, sources


def test_npm_overlay_restart_policies_survive_a_reboot() -> None:
    """`on-failure` does not come back after a host reboot or a Docker upgrade.

    The shared release gate (`validate-deployed-overlay`) requires `unless-stopped` on every
    long-running service and `"no"` on the run-once init.
    """
    npm = _compose("docker-compose.npm.yml")
    assert npm["services"]["clingen_link"]["restart"] == "unless-stopped"
    assert npm["services"]["clingen_data_init"]["restart"] == "no"
    for service in npm["services"].values():
        assert "restart_policy" not in service.get("deploy", {}), (
            "deploy.restart_policy silently overrides restart:"
        )


def test_container_release_declares_the_deployed_overlay_and_every_seed_bind() -> None:
    """The gate refuses an undeclared host bind, and defaults would hide a layered stack."""
    service = _release_config()["service"]
    assert service["deployed_compose_files"] == ["docker/docker-compose.npm.yml"]
    npm = _compose("docker-compose.npm.yml")
    binds = {
        mount["target"]
        for definition in npm["services"].values()
        for mount in definition.get("volumes", [])
        if mount.get("type") == "bind"
    }
    assert binds == set(service["deployed_seed_binds"]), (
        "every read-only bind in the deployed overlay must be declared in "
        "container-release.json service.deployed_seed_binds"
    )
    for definition in npm["services"].values():
        for mount in definition.get("volumes", []):
            if mount.get("type") == "bind":
                assert mount["read_only"] is True, "a declared seed bind is never writable"
