"""Container & Deployment Hardening Standard v1: the base compose is safe-by-default.

The prod/npm overlays were already hardened (clingen-link is Tier A); this guards
universal gap #1 — running the *base* compose directly must not drop controls or
publish an auth=none backend on 0.0.0.0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DOCKER = Path(__file__).resolve().parents[2] / "docker"


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that understands the Compose-spec merge tag `!reset`.

    Overlay files use `!reset` to clear inherited base values instead of
    merging them. `!reset` isn't a standard YAML tag, so plain
    `yaml.safe_load` raises `ConstructorError` on it; we only need the
    underlying value for these assertions.
    """


def _construct_reset(loader: _ComposeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!reset", _construct_reset)


def _service(compose_file: str, service: str) -> dict[str, Any]:
    text = (DOCKER / compose_file).read_text(encoding="utf-8")
    data = yaml.load(text, Loader=_ComposeLoader)  # noqa: S506 - subclasses SafeLoader
    return data["services"][service]


def test_base_compose_is_hardened() -> None:
    svc = _service("docker-compose.yml", "clingen-link")
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["init"] is True
    assert svc["pids_limit"] == 256
    assert svc["deploy"]["resources"]["limits"]["memory"]


def test_base_compose_binds_loopback_only() -> None:
    svc = _service("docker-compose.yml", "clingen-link")
    # An auth=none backend must never be published on 0.0.0.0.
    assert svc["ports"], "base compose must still publish a loopback port for the quick-start"
    assert all(str(p).startswith("127.0.0.1:") for p in svc["ports"]), svc["ports"]


def test_prod_overlay_inherits_base_tmpfs() -> None:
    # The prod overlay shares the base service key (clingen-link) and is deployed
    # LAYERED over docker-compose.yml, so it inherits the base tmpfs. Redeclaring
    # it would make Compose list-merge yield a duplicate /tmp/clingen-link mount.
    assert "tmpfs" not in _service("docker-compose.prod.yml", "clingen-link")


def test_npm_overlay_is_self_contained_with_writable_tmpfs() -> None:
    # The npm overlay is deployed as a SINGLE, self-contained compose file (the
    # GeneFoundry -link fleet standard) under its own service key (clingen_link).
    # It is NOT layered over docker-compose.yml, and the differing service key
    # means it could not inherit the base tmpfs even if it were. Under read_only
    # it MUST therefore declare its own writable /tmp/clingen-link tmpfs, or the
    # snapshot .zst decompress at startup (store/db.py) crash-loops with
    # "No usable temporary directory".
    svc = _service("docker-compose.npm.yml", "clingen_link")
    assert svc["read_only"] is True
    tmpfs = svc.get("tmpfs", [])
    # S108 is a false positive here: this asserts a compose mount target, it is
    # not a runtime use of a hardcoded temporary directory.
    assert any("/tmp/clingen-link" in str(entry) for entry in tmpfs), tmpfs  # noqa: S108
