from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_authoritative_snapshot_is_not_tracked_or_packaged() -> None:
    data = ROOT / "clingen_link" / "data"
    assert sorted(path.name for path in data.iterdir()) == ["svi_guidance.json"]
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "clingen.sqlite" not in pyproject


def test_docker_context_excludes_authoritative_data() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "clingen_link/data/*" in ignored
    assert "!clingen_link/data/svi_guidance.json" in ignored
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "clingen.sqlite" not in dockerfile
    production = (ROOT / "docker/docker-compose.prod.yml").read_text(encoding="utf-8")
    assert production.count("build: !reset null") == 2
    assert "image@sha256" in production


def test_vendored_contract_matches_recorded_router_feature_commit() -> None:
    vendor = ROOT / "vendor" / "genefoundry"
    lines = (vendor / "CONTRACT_SHA256").read_text(encoding="utf-8").splitlines()
    expected = lines[0].split()[0]
    actual = hashlib.sha256((vendor / "data-release-manifest.schema.json").read_bytes()).hexdigest()
    assert actual == expected
    assert lines[1].split()[0] == "a0650fce7205a21f4eee68a7b9de4e69292d6db7"


def assert_oci_archive_has_no_snapshot(layout: Path) -> None:
    """Inspect every tar member, including whiteouted history, for data blobs."""
    with tarfile.open(layout) as outer:
        for member in outer.getmembers():
            name = member.name.lower()
            assert "clingen.sqlite" not in name
            assert not name.endswith((".sqlite", ".sqlite.zst"))
