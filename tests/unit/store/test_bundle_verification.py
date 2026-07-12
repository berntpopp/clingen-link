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

from clingen_link.config import settings
from clingen_link.exceptions import SnapshotUnavailableError
from clingen_link.store.db import (
    _BUNDLED_ZST_PATH,
    _BUNDLED_ZST_SHA256,
    _MAX_EXPANDED_BYTES,
    Store,
)


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


class TestCommittedAnchor:
    """The committed digest is a correct, independent authenticity anchor."""

    def test_constant_matches_shipped_bundle(self) -> None:
        # Authenticity happy path + drift guard: the committed constant is the
        # real sha256 of the packaged artifact, so the shipped bundle verifies.
        actual = hashlib.sha256(_BUNDLED_ZST_PATH.read_bytes()).hexdigest()
        assert actual == _BUNDLED_ZST_SHA256

    def test_ceiling_is_generous_but_bounded(self) -> None:
        # Above the real ~58.6 MiB snapshot, well below an unbounded expansion.
        assert _MAX_EXPANDED_BYTES > 61_480_960
        assert _MAX_EXPANDED_BYTES <= 512 * 1024 * 1024

    def test_shipped_bundle_opens_via_constant(self) -> None:
        # End-to-end: the real packaged bundle passes the constant-anchored
        # check and yields a usable read-only store.
        with Store(_BUNDLED_ZST_PATH) as store:
            meta = store.meta()
        assert "validity" in meta


class TestChecksumMismatch:
    """A tampered bundle must fail closed BEFORE decompression."""

    def test_wrong_sidecar_fails_closed(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        _write_sidecar(bundle, "0" * 64)  # deliberately wrong digest

        # Prove fail-closed happens BEFORE decompression: if the guard ran the
        # decompressor at all this sentinel would raise a *different* error.
        def _boom(*_a: object, **_k: object) -> object:
            raise AssertionError("decompression must not start on a bad digest")

        monkeypatch.setattr(zstandard, "ZstdDecompressor", _boom)

        with pytest.raises(SnapshotUnavailableError):
            Store(bundle)

    def test_malformed_sidecar_fails_closed(self, test_snapshot_path: Path, tmp_path: Path) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        bundle.with_suffix(".sha256").write_text("not-a-valid-digest\n")
        with pytest.raises(SnapshotUnavailableError):
            Store(bundle)


class TestTruncation:
    """A truncated bundle changes the digest and must fail closed."""

    def test_truncated_bundle_fails_closed(self, test_snapshot_path: Path, tmp_path: Path) -> None:
        bundle = _make_bundle(test_snapshot_path, tmp_path)
        good = hashlib.sha256(bundle.read_bytes()).hexdigest()
        _write_sidecar(bundle, good)  # correct sidecar for the intact bundle
        data = bundle.read_bytes()
        bundle.write_bytes(data[: len(data) // 2])  # now truncated
        with pytest.raises(SnapshotUnavailableError):
            Store(bundle)


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
        monkeypatch.setattr(settings, "snapshot_zst_sha256", "", raising=False)
        bundle = _make_bundle(test_snapshot_path, tmp_path)  # deliberately no sidecar

        def _boom(*_a: object, **_k: object) -> object:
            raise AssertionError("decompression must not start without a verified digest")

        monkeypatch.setattr(zstandard, "ZstdDecompressor", _boom)

        with pytest.raises(SnapshotUnavailableError):
            Store(bundle)

    def test_configured_digest_match_opens(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The "or config" anchor: an operator pins the expected digest out of
        # band. A matching bundle verifies and opens as a usable store.
        bundle = _make_bundle(test_snapshot_path, tmp_path)  # no sidecar
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        monkeypatch.setattr(settings, "snapshot_zst_sha256", digest, raising=False)
        with Store(bundle) as store:
            meta = store.meta()
        assert "validity" in meta

    def test_configured_digest_mismatch_fails_closed(
        self,
        test_snapshot_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A configured-but-wrong digest fails closed BEFORE decompression.
        bundle = _make_bundle(test_snapshot_path, tmp_path)  # no sidecar
        monkeypatch.setattr(settings, "snapshot_zst_sha256", "0" * 64, raising=False)

        def _boom(*_a: object, **_k: object) -> object:
            raise AssertionError("decompression must not start on a bad configured digest")

        monkeypatch.setattr(zstandard, "ZstdDecompressor", _boom)

        with pytest.raises(SnapshotUnavailableError):
            Store(bundle)


class TestDecompressionBomb:
    """A bundle that expands past the ceiling must fail closed even if authentic."""

    def test_bomb_over_ceiling_fails_closed(self, tmp_path: Path) -> None:
        bomb = tmp_path / "clingen.sqlite.zst"
        cctx = zstandard.ZstdCompressor(level=1)
        chunk = b"\x00" * (1024 * 1024)
        target = _MAX_EXPANDED_BYTES + 4 * 1024 * 1024
        written = 0
        with bomb.open("wb") as fh, cctx.stream_writer(fh) as writer:
            while written < target:
                writer.write(chunk)
                written += len(chunk)

        # Correct sidecar → passes integrity; must still fail on expanded size.
        digest = hashlib.sha256(bomb.read_bytes()).hexdigest()
        _write_sidecar(bomb, digest)
        with pytest.raises(SnapshotUnavailableError):
            Store(bomb)
