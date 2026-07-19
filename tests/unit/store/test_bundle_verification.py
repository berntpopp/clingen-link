"""F-16: the packaged ``.zst`` snapshot is authenticity-verified and bomb-guarded.

The store decompresses the shipped bundle at startup. Before it does, it must
**fail closed** when the packaged artifact has been tampered (checksum
mismatch), truncated, or is a decompression bomb — defense in depth against
package/artifact tampering. Authenticity is anchored to a committed in-repo
constant (``_BUNDLED_ZST_SHA256``), independent of the same-directory
``.sha256`` sidecar a tamperer could rewrite.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import pytest
import zstandard

from clingen_link.config import DataRequirement
from clingen_link.data_contract import SNAPSHOT_SCHEMA_SEMVER
from clingen_link.exceptions import SnapshotUnavailableError
from clingen_link.runtime_data_identity import RuntimeDataIdentityError, verify_runtime_identity
from clingen_link.store.db import (
    Store,
    _materialized_version_key,
    _write_runtime_identity_manifest,
    canonical_expanded_digest,
    materialize_bundle,
)

_RUNTIME_HELPER_SHA256 = "61846310051968583b6c236824cec5c9b585e82170a62fe55fd92f3c70cfc7f1"
_DATA_RELEASE_TAG = "data-clingen-2026-07-16"


def test_runtime_data_identity_helper_matches_router_pin() -> None:
    helper = Path(__file__).resolve().parents[3] / "clingen_link" / "runtime_data_identity.py"
    assert hashlib.sha256(helper.read_bytes()).hexdigest() == _RUNTIME_HELPER_SHA256


def _write_sidecar(bundle: Path, digest: str) -> Path:
    """Write a production-shaped ``<digest>  <name>`` sidecar next to ``bundle``."""
    sidecar = bundle.with_suffix(".sha256")
    sidecar.write_text(f"{digest}  {bundle.name}\n")
    return sidecar


def _make_bundle(test_snapshot_path: Path, tmp_path: Path) -> Path:
    """Compress the tiny test snapshot into a real ``.zst`` bundle."""
    raw = test_snapshot_path.read_bytes()
    bundle = tmp_path / "clingen.sqlite.zst"
    bundle.write_bytes(zstandard.ZstdCompressor().compress(raw))
    return bundle


def _requirement(bundle: Path, raw: Path) -> DataRequirement:
    return DataRequirement(
        bundle_path=bundle,
        release_tag=_DATA_RELEASE_TAG,
        compressed_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
        expanded_tree_sha256=canonical_expanded_digest(raw, member_name="clingen.sqlite"),
        schema_version=SNAPSHOT_SCHEMA_SEMVER,
        schema_minimum=SNAPSHOT_SCHEMA_SEMVER,
        schema_maximum=SNAPSHOT_SCHEMA_SEMVER,
        max_compressed_bytes=4 * 1024 * 1024,
        max_expanded_bytes=16 * 1024 * 1024,
    )


def _runtime_root(test_snapshot_path: Path, tmp_path: Path) -> tuple[Path, list[Path]]:
    root = tmp_path / "runtime"
    root.mkdir()
    snapshot = root / "clingen.sqlite"
    shutil.copyfile(test_snapshot_path, snapshot)
    identity = root / "identity.json"
    identity.write_text('{"mode":"test"}\n', encoding="utf-8")
    return root, [snapshot, identity]


class TestExternalMaterialization:
    def test_version_key_uses_the_full_bundle_digest(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path)
        colliding_prefix = requirement.model_copy(
            update={"compressed_sha256": requirement.compressed_sha256[:16] + "f" * 48}
        )

        assert _materialized_version_key(requirement) != _materialized_version_key(colliding_prefix)

    def test_materialization_writes_a_verified_runtime_identity_manifest(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path)

        selected = materialize_bundle(requirement, tmp_path / "reference")

        manifest_path = selected.with_name("data-identity-manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["release_tag"] == _DATA_RELEASE_TAG
        assert [item["path"] for item in manifest["inputs"]] == [
            "clingen.sqlite",
            "identity.json",
        ]
        assert verify_runtime_identity(selected.parent)["release_tag"] == _DATA_RELEASE_TAG

    def test_corrupt_materialized_snapshot_cannot_produce_runtime_identity(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path)
        selected = materialize_bundle(requirement, tmp_path / "reference")
        selected.chmod(0o644)
        content = bytearray(selected.read_bytes())
        content[len(content) // 2] ^= 0x01
        selected.write_bytes(content)

        with pytest.raises(RuntimeDataIdentityError, match="sha256"):
            verify_runtime_identity(selected.parent)

    def test_exact_bundle_materializes_versioned_snapshot(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path)

        selected = materialize_bundle(requirement, tmp_path / "reference")

        assert selected.name == "clingen.sqlite"
        assert selected.parent.name.startswith(f"{requirement.compressed_sha256[:16]}-")
        assert selected.read_bytes() == test_snapshot_path.read_bytes()
        assert (tmp_path / "reference" / "current").resolve() == selected.parent
        with Store(selected) as store:
            assert set(store.meta()) >= {"validity", "dosage"}

    def test_same_bundle_with_a_new_release_tag_never_mutates_the_old_version(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        root = tmp_path / "reference"
        first_requirement = _requirement(bundle, test_snapshot_path).model_copy(
            update={"release_tag": "data-clingen-a"}
        )
        selected_a = materialize_bundle(first_requirement, root)
        manifest_a = selected_a.with_name("data-identity-manifest.json")
        manifest_a_bytes = manifest_a.read_bytes()
        identity_a = verify_runtime_identity(selected_a.parent)

        second_requirement = first_requirement.model_copy(update={"release_tag": "data-clingen-b"})
        selected_b = materialize_bundle(second_requirement, root)

        assert selected_b.parent != selected_a.parent
        assert selected_a.exists()
        assert manifest_a.read_bytes() == manifest_a_bytes
        assert verify_runtime_identity(selected_a.parent) == identity_a
        identity_b = verify_runtime_identity(selected_b.parent)
        assert identity_b["release_tag"] == "data-clingen-b"
        assert identity_b["digest"] != identity_a["digest"]
        assert (root / "current").resolve() == selected_b.parent

    def test_materialization_recovers_only_exact_stale_scratch_without_following_symlinks(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path)
        root = tmp_path / "reference"
        root.mkdir()
        version_key = _materialized_version_key(requirement)

        (root / ".verified-bundle-1").write_bytes(b"stale verified bytes")
        stale_staging = root / f".{version_key}.staging-1"
        stale_staging.mkdir()
        (stale_staging / "clingen.sqlite").write_bytes(b"partial")
        legacy_staging_dir = root / f".{requirement.compressed_sha256[:16]}.staging-1"
        legacy_staging_dir.mkdir()
        (legacy_staging_dir / "clingen.sqlite").write_bytes(b"legacy partial")
        legacy_staging_file = root / f".{requirement.compressed_sha256[:16]}.staging-2"
        legacy_staging_file.write_bytes(b"legacy partial file")
        external_file = tmp_path / "external-sentinel"
        external_file.write_text("preserve", encoding="utf-8")
        (stale_staging / "external-link").symlink_to(external_file)
        external_dir = tmp_path / "external-directory"
        external_dir.mkdir()
        (external_dir / "sentinel").write_text("preserve", encoding="utf-8")
        (root / f".{version_key}.staging-deadbeef").symlink_to(
            external_dir, target_is_directory=True
        )
        nonmatching = root / ".verified-bundle-not!scratch"
        nonmatching.write_text("preserve", encoding="utf-8")
        near_legacy = root / f".{requirement.compressed_sha256[:15]}.staging-3"
        near_legacy.write_text("preserve", encoding="utf-8")

        selected = materialize_bundle(requirement, root)

        assert selected.exists()
        assert not (root / ".verified-bundle-1").exists()
        assert not stale_staging.exists()
        assert not legacy_staging_dir.exists()
        assert not legacy_staging_file.exists()
        assert not (root / f".{version_key}.staging-deadbeef").exists()
        assert external_file.read_text(encoding="utf-8") == "preserve"
        assert (external_dir / "sentinel").read_text(encoding="utf-8") == "preserve"
        assert nonmatching.read_text(encoding="utf-8") == "preserve"
        assert near_legacy.read_text(encoding="utf-8") == "preserve"

    def test_identity_file_is_fsynced_before_staging_directory_publication(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path)
        root = tmp_path / "reference"
        events: list[tuple[str, str]] = []
        real_fsync = os.fsync
        real_replace = os.replace

        def tracked_fsync(fd: int) -> None:
            target = os.readlink(f"/proc/self/fd/{fd}")
            kind = "fsync-dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync-file"
            events.append((kind, target))
            real_fsync(fd)

        def tracked_replace(source: str | Path, destination: str | Path) -> None:
            events.append(("replace", str(destination)))
            real_replace(source, destination)

        monkeypatch.setattr("clingen_link.store.db.os.fsync", tracked_fsync)
        monkeypatch.setattr("clingen_link.store.db.os.replace", tracked_replace)

        selected = materialize_bundle(requirement, root)

        identity_replace = next(
            index
            for index, event in enumerate(events)
            if event[0] == "replace" and event[1].endswith("/identity.json")
        )
        identity_fsync = next(
            index
            for index, event in enumerate(events)
            if event[0] == "fsync-file" and event[1].endswith("/identity.json")
        )
        staging_directory_fsync = next(
            index
            for index, event in enumerate(events)
            if event[0] == "fsync-dir" and ".staging-" in event[1]
        )
        version_publish = next(
            index
            for index, event in enumerate(events)
            if event[0] == "replace" and event[1] == str(selected.parent)
        )
        assert identity_replace < identity_fsync < staging_directory_fsync < version_publish


class TestRuntimeManifestDurability:
    def test_stale_temporary_manifest_is_replaced_and_permissions_are_read_only(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        root, files = _runtime_root(test_snapshot_path, tmp_path)
        temporary = root / "data-identity-manifest.json.tmp"
        temporary.write_text("stale", encoding="utf-8")
        temporary.chmod(0o444)

        _write_runtime_identity_manifest(root, _DATA_RELEASE_TAG, files)

        destination = root / "data-identity-manifest.json"
        assert not temporary.exists()
        assert stat.S_IMODE(destination.stat().st_mode) == 0o444
        assert verify_runtime_identity(root)["release_tag"] == _DATA_RELEASE_TAG

    def test_manifest_file_and_directory_are_fsynced_around_atomic_replace(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root, files = _runtime_root(test_snapshot_path, tmp_path)
        events: list[str] = []
        real_fsync = os.fsync
        real_replace = os.replace

        def tracked_fsync(fd: int) -> None:
            kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
            events.append(f"fsync-{kind}")
            real_fsync(fd)

        def tracked_replace(source: str | Path, destination: str | Path) -> None:
            events.append("replace")
            real_replace(source, destination)

        monkeypatch.setattr("clingen_link.store.db.os.fsync", tracked_fsync)
        monkeypatch.setattr("clingen_link.store.db.os.replace", tracked_replace)

        _write_runtime_identity_manifest(root, _DATA_RELEASE_TAG, files)

        assert events.index("fsync-file") < events.index("replace") < events.index("fsync-dir")

    def test_failed_manifest_replace_preserves_destination_and_recovers_cleanly(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root, files = _runtime_root(test_snapshot_path, tmp_path)
        _write_runtime_identity_manifest(root, _DATA_RELEASE_TAG, files)
        destination = root / "data-identity-manifest.json"
        original = destination.read_bytes()
        real_replace = os.replace

        def fail_replace(_source: str | Path, _destination: str | Path) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr("clingen_link.store.db.os.replace", fail_replace)
        with pytest.raises(OSError, match="simulated replace failure"):
            _write_runtime_identity_manifest(root, "data-clingen-retry", files)

        assert destination.read_bytes() == original
        assert not (root / "data-identity-manifest.json.tmp").exists()
        monkeypatch.setattr("clingen_link.store.db.os.replace", real_replace)
        _write_runtime_identity_manifest(root, "data-clingen-retry", files)
        assert verify_runtime_identity(root)["release_tag"] == "data-clingen-retry"

    def test_expanded_identity_mismatch_leaves_current_unchanged(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path)
        root = tmp_path / "reference"
        selected = materialize_bundle(requirement, root)
        bad = requirement.model_copy(update={"expanded_tree_sha256": "0" * 64})

        with pytest.raises(SnapshotUnavailableError, match="identity"):
            materialize_bundle(bad, root)

        assert (root / "current").resolve() == selected.parent

    def test_tampered_existing_materialization_is_rejected(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path)
        root = tmp_path / "reference"
        selected = materialize_bundle(requirement, root)
        selected.chmod(0o644)
        selected.write_bytes(b"tampered")

        with pytest.raises(SnapshotUnavailableError, match="expanded-tree"):
            materialize_bundle(requirement, root)


class TestChecksumMismatch:
    """A tampered bundle must fail closed BEFORE decompression."""

    def test_wrong_sidecar_fails_closed(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path).model_copy(
            update={"compressed_sha256": "0" * 64}
        )

        # Prove fail-closed happens BEFORE decompression: if the guard ran the
        # decompressor at all this sentinel would raise a *different* error.
        def _boom(*_a: object, **_k: object) -> object:
            raise AssertionError("decompression must not start on a bad digest")

        monkeypatch.setattr(zstandard, "ZstdDecompressor", _boom)

        with pytest.raises(SnapshotUnavailableError):
            materialize_bundle(requirement, tmp_path / "reference")

    def test_malformed_sidecar_fails_closed(self, test_snapshot_path: Path, tmp_path: Path) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        with pytest.raises(ValueError):
            DataRequirement(
                bundle_path=bundle,
                compressed_sha256="invalid",
                expanded_tree_sha256="0" * 64,
                schema_version=SNAPSHOT_SCHEMA_SEMVER,
                schema_minimum=SNAPSHOT_SCHEMA_SEMVER,
                schema_maximum=SNAPSHOT_SCHEMA_SEMVER,
                max_compressed_bytes=1024,
                max_expanded_bytes=1024,
            )


class TestTruncation:
    """A truncated bundle changes the digest and must fail closed."""

    def test_truncated_bundle_fails_closed(self, test_snapshot_path: Path, tmp_path: Path) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path)
        data = bundle.read_bytes()
        bundle.write_bytes(data[: len(data) // 2])  # now truncated
        with pytest.raises(SnapshotUnavailableError):
            materialize_bundle(requirement, tmp_path / "reference")


class TestUnverifiedNonPackagedBundle:
    """F-16 residual: a non-packaged / operator-refresh ``.zst`` must NOT be
    decompressed without a digest anchor. The authenticity check was previously
    only applied to the shipped bundle (committed constant) or to bundles that
    happened to carry a ``.sha256`` sidecar; an alternate / operator-refresh
    bundle with neither slipped straight into the decompressor unverified.
    """

    def test_no_anchor_fails_closed_before_decompress(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A non-shipped bundle with NO sidecar and NO configured digest: the
        # operator-refresh / alternate path Codex flagged. It must fail closed
        # and must not reach the decompressor at all.
        bundle = _make_bundle(test_snapshot_path, tmp_path)  # deliberately no sidecar

        def _boom(*_a: object, **_k: object) -> object:
            raise AssertionError("decompression must not start without a verified digest")

        monkeypatch.setattr(zstandard, "ZstdDecompressor", _boom)

        with pytest.raises(ValueError):
            DataRequirement(
                bundle_path=bundle,
                compressed_sha256="",
                expanded_tree_sha256="0" * 64,
                schema_version=SNAPSHOT_SCHEMA_SEMVER,
                schema_minimum=SNAPSHOT_SCHEMA_SEMVER,
                schema_maximum=SNAPSHOT_SCHEMA_SEMVER,
                max_compressed_bytes=1024,
                max_expanded_bytes=1024,
            )

    def test_configured_digest_match_opens(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The "or config" anchor: an operator pins the expected digest out of
        # band. A matching bundle verifies and opens as a usable store.
        bundle = _make_bundle(test_snapshot_path, tmp_path)  # no sidecar
        requirement = _requirement(bundle, test_snapshot_path)
        assert materialize_bundle(requirement, tmp_path / "reference").exists()

    def test_configured_digest_mismatch_fails_closed(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A configured-but-wrong digest fails closed BEFORE decompression.
        bundle = _make_bundle(test_snapshot_path, tmp_path)  # no sidecar
        requirement = _requirement(bundle, test_snapshot_path).model_copy(
            update={"compressed_sha256": "0" * 64}
        )

        def _boom(*_a: object, **_k: object) -> object:
            raise AssertionError("decompression must not start on a bad configured digest")

        monkeypatch.setattr(zstandard, "ZstdDecompressor", _boom)

        with pytest.raises(SnapshotUnavailableError):
            materialize_bundle(requirement, tmp_path / "reference")


class TestDecompressionBomb:
    """A bundle that expands past the ceiling must fail closed even if authentic."""

    def test_bomb_over_ceiling_fails_closed(self, tmp_path: Path) -> None:
        bomb = tmp_path / "clingen.sqlite.zst"
        cctx = zstandard.ZstdCompressor(level=1)
        chunk = b"\x00" * (1024 * 1024)
        target = 2 * 1024 * 1024
        written = 0
        with bomb.open("wb") as fh, cctx.stream_writer(fh) as writer:
            while written < target:
                writer.write(chunk)
                written += len(chunk)

        requirement = DataRequirement(
            bundle_path=bomb,
            release_tag=_DATA_RELEASE_TAG,
            compressed_sha256=hashlib.sha256(bomb.read_bytes()).hexdigest(),
            expanded_tree_sha256="0" * 64,
            schema_version=SNAPSHOT_SCHEMA_SEMVER,
            schema_minimum=SNAPSHOT_SCHEMA_SEMVER,
            schema_maximum=SNAPSHOT_SCHEMA_SEMVER,
            max_compressed_bytes=1024 * 1024,
            max_expanded_bytes=1024 * 1024,
        )
        with pytest.raises(SnapshotUnavailableError):
            materialize_bundle(requirement, tmp_path / "reference")
