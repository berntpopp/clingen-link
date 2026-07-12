"""F-19: the builder must not bootstrap a floating pip/uv installer.

The uv installer is pinned by a digest-addressed ``COPY --from`` of the official
image so the build is reproducible; no unbounded ``pip install --upgrade``.
"""

from __future__ import annotations

from pathlib import Path

_DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile"

_UV_COPY = (
    "COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:"
    "1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab "
    "/uv /usr/local/bin/uv"
)


def test_dockerfile_pins_uv_and_has_no_floating_pip_upgrade() -> None:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert "pip install --upgrade" not in text, "floating pip/uv upgrade must be removed"
    assert _UV_COPY in text, "uv installer must be a digest-pinned COPY --from"
