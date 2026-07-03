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

    Overlay files (e.g. `docker-compose.prod.yml`) use `ports: !reset []` to
    clear a base-declared sequence instead of appending to it. `!reset` isn't
    a standard YAML tag, so plain `yaml.safe_load` raises `ConstructorError`
    on it; we only need the underlying value for these assertions.
    """


_ComposeLoader.add_constructor("!reset", yaml.SafeLoader.construct_sequence)


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


def test_overlays_do_not_redeclare_tmpfs() -> None:
    # Base owns the tmpfs; an overlay re-adding /tmp/clingen-link makes Compose
    # list-merge yield a duplicate mount target. (Service keys differ by file.)
    assert "tmpfs" not in _service("docker-compose.prod.yml", "clingen-link")
    assert "tmpfs" not in _service("docker-compose.npm.yml", "clingen_link")
