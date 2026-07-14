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
from pathlib import Path

import pytest
import zstandard

from clingen_link.config import DataRequirement
from clingen_link.data_contract import SNAPSHOT_SCHEMA_SEMVER
from clingen_link.exceptions import SnapshotUnavailableError
from clingen_link.store.db import Store, canonical_expanded_digest, materialize_bundle


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
        compressed_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
        expanded_tree_sha256=canonical_expanded_digest(raw, member_name="clingen.sqlite"),
        schema_version=SNAPSHOT_SCHEMA_SEMVER,
        schema_minimum=SNAPSHOT_SCHEMA_SEMVER,
        schema_maximum=SNAPSHOT_SCHEMA_SEMVER,
        max_compressed_bytes=4 * 1024 * 1024,
        max_expanded_bytes=16 * 1024 * 1024,
    )


class TestExternalMaterialization:
    def test_exact_bundle_materializes_versioned_snapshot(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        requirement = _requirement(bundle, test_snapshot_path)

        selected = materialize_bundle(requirement, tmp_path / "reference")

        assert selected.name == "clingen.sqlite"
        assert selected.parent.name == requirement.compressed_sha256[:16]
        assert selected.read_bytes() == test_snapshot_path.read_bytes()
        assert (tmp_path / "reference" / "current").resolve() == selected.parent
        with Store(selected) as store:
            assert set(store.meta()) >= {"validity", "dosage"}

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
