from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from clingen_link.etl.release_identity import (
    ReleaseIdentityError,
    assert_exact_assets,
    load_sealed_identity,
    release_state,
    sealed_identity,
)


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "dataset": {
            "source": {
                "identifier": "ClinGen-2026.07",
                "url": "https://clinicalgenome.org/",
                "sha256": "a" * 64,
                "retrieved_at": "volatile",
            }
        },
        "schema": {"actual": "2.0.0"},
        "record_counts": {"validity": 4, "dosage": 2},
        "artifact": {
            "filename": "clingen.sqlite.zst",
            "sha256": "b" * 64,
            "compressed_size": 12,
            "expanded_tree_sha256": "c" * 64,
            "expanded_size": 22,
            "member_count": 1,
        },
    }


def test_identity_is_stable_and_excludes_capture_time() -> None:
    first = _manifest()
    second = _manifest()
    second["dataset"] = {"source": {**first["dataset"]["source"], "retrieved_at": "later"}}  # type: ignore[index]
    assert sealed_identity(first) == sealed_identity(second)
    assert sealed_identity(first).tag == "data-clingen-" + ("a" * 16)


@pytest.mark.parametrize("field", ["sha256", "compressed_size", "record_counts"])
def test_identity_rejects_missing_or_wrong_stable_fields(field: str) -> None:
    manifest = _manifest()
    if field == "sha256":
        manifest["artifact"] = {**manifest["artifact"], field: 1}  # type: ignore[arg-type]
    elif field == "compressed_size":
        manifest["artifact"] = {
            key: value for key, value in manifest["artifact"].items() if key != field
        }  # type: ignore[union-attr]
    else:
        manifest.pop(field)
    with pytest.raises(ReleaseIdentityError):
        sealed_identity(manifest)


def test_state_is_closed_and_never_replaces_a_collision() -> None:
    identity = sealed_identity(_manifest())
    assert release_state(identity, None, None) == "create"
    assert release_state(identity, identity, False) == "published_noop"
    assert release_state(identity, identity, True) == "draft_publish_existing"
    other = sealed_identity(
        {**_manifest(), "artifact": {**_manifest()["artifact"], "sha256": "d" * 64}}
    )  # type: ignore[arg-type]
    assert release_state(identity, other, False) == "collision"
    assert release_state(identity, other, True) == "collision"


def test_manifest_file_is_capped_and_checksum_bound(tmp_path: Path) -> None:
    manifest = _manifest()
    payload = json.dumps(manifest).encode()
    (tmp_path / "data-release-manifest.json").write_bytes(payload)
    (tmp_path / "clingen.sqlite.zst").write_bytes(b"bundle")
    sums = f"{hashlib.sha256(b'bundle').hexdigest()}  clingen.sqlite.zst\n{hashlib.sha256(payload).hexdigest()}  data-release-manifest.json\n"
    (tmp_path / "SHA256SUMS").write_text(sums)
    # The pure parser validates the exact stable fields; workflow validates the complete asset set.
    assert (
        sealed_identity(
            json.loads((tmp_path / "data-release-manifest.json").read_text())
        ).source_sha256
        == "a" * 64
    )


def test_identity_rejects_symlink_or_extra_remote_assets(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps(_manifest()))
    link = tmp_path / "manifest.json"
    link.symlink_to(source)
    with pytest.raises(ReleaseIdentityError):
        load_sealed_identity(link)
    with pytest.raises(ReleaseIdentityError):
        assert_exact_assets(
            ["clingen.sqlite.zst", "data-release-manifest.json", "SHA256SUMS", "extra"]
        )
