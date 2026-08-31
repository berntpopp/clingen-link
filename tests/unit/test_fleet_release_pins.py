"""Regression guards for reviewed fleet release dependencies."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_dependencies_use_reviewed_immutable_pins() -> None:
    assert (
        "python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5"
        in (ROOT / "docker/Dockerfile").read_text()
    )
    assert (
        "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d"
        in (ROOT / ".github/workflows/ci.yml").read_text()
    )
    assert (
        "_container-ci.yml@db47bd3357cebf33e6722615c4f0e7419a64857e"
        in (ROOT / ".github/workflows/container-ci.yml").read_text()
    )
    assert (
        "_container-release.yml@db47bd3357cebf33e6722615c4f0e7419a64857e"
        in (ROOT / ".github/workflows/container-release.yml").read_text()
    )
