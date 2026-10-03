"""Regression guards for reviewed fleet release dependencies."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_dependencies_use_reviewed_immutable_pins() -> None:
    assert (
        "python:3.14-slim@sha256:0741d101873c12ab927e6f8653feb8862b9bd58771177acb1b885b95141f91b4"
        in (ROOT / "docker/Dockerfile").read_text()
    )
    assert (
        "apt-get install -y --only-upgrade --no-install-recommends"
        in (ROOT / "docker/Dockerfile").read_text()
    )
    assert (
        "astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7"
        in (ROOT / ".github/workflows/ci.yml").read_text()
    )
    assert (
        "_container-ci.yml@0122f6e6d8f6a9057b80134d7cacbf61c5bd2e84"
        in (ROOT / ".github/workflows/container-ci.yml").read_text()
    )
    assert (
        "_container-release.yml@0122f6e6d8f6a9057b80134d7cacbf61c5bd2e84"
        in (ROOT / ".github/workflows/container-release.yml").read_text()
    )


def test_production_compose_uses_the_approved_restart_policy() -> None:
    compose = (ROOT / "docker/docker-compose.prod.yml").read_text()
    assert "restart: unless-stopped" in compose
    assert "restart: on-failure" not in compose
