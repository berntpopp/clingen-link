"""Regression guards for reviewed fleet release dependencies."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_dependencies_use_reviewed_immutable_pins() -> None:
    assert (
        "python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5"
        in (ROOT / "docker/Dockerfile").read_text()
    )
    assert (
        "apt-get install -y --only-upgrade --no-install-recommends"
        in (ROOT / "docker/Dockerfile").read_text()
    )
    assert (
        "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d"
        in (ROOT / ".github/workflows/ci.yml").read_text()
    )
    assert (
        "_container-ci.yml@3d3cc20477828ddbd8a0c980b5b4f709e2612c02"
        in (ROOT / ".github/workflows/container-ci.yml").read_text()
    )
    assert (
        "_container-release.yml@3d3cc20477828ddbd8a0c980b5b4f709e2612c02"
        in (ROOT / ".github/workflows/container-release.yml").read_text()
    )


def test_production_compose_uses_the_approved_restart_policy() -> None:
    compose = (ROOT / "docker/docker-compose.prod.yml").read_text()
    assert "restart: unless-stopped" in compose
    assert "restart: on-failure" not in compose
