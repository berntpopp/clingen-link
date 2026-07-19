"""Tests for the read-only SQLite store: open, resolve, freshness."""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from clingen_link.exceptions import SnapshotUnavailableError
from clingen_link.runtime_data_identity import build_identity_manifest, canonical_json_bytes
from clingen_link.store.db import Store


def _write_version(root: Path, name: str, source: Path, marker: str) -> Path:
    version = root / name
    version.mkdir()
    snapshot = version / "clingen.sqlite"
    shutil.copyfile(source, snapshot)
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE selected_version (marker TEXT NOT NULL)")
        connection.execute("INSERT INTO selected_version VALUES (?)", (marker,))
    manifest = build_identity_manifest(version, f"data-clingen-{name}", [snapshot])
    (version / "data-identity-manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    return version


def _select(root: Path, version: Path) -> None:
    staged = root / ".current.tmp"
    staged.symlink_to(version.name, target_is_directory=True)
    os.replace(staged, root / "current")


class TestMetaRecordCount:
    """record_count must reflect actual rows, not a stored/derived value (assessment H2)."""

    def test_record_count_matches_table_count(self, store: Store) -> None:
        meta = store.meta()
        with store.connection() as conn:
            for domain, table in (
                ("validity", "validity"),
                ("dosage", "dosage"),
                ("actionability", "actionability"),
                ("erepo", "erepo"),
            ):
                actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
                assert meta[domain]["record_count"] == actual

    def test_dosage_count_is_rows_not_etag_count(self, store: Store) -> None:
        # The fixture supplies 2 dosage ETags; the served count must be the real row count.
        meta = store.meta()
        with store.connection() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM dosage").fetchone()[0]
        assert meta["dosage"]["record_count"] == rows
        if rows != 2:  # guards against the old "len(etags)" bug coincidentally matching
            assert meta["dosage"]["record_count"] != 2


class TestGeneResolution:
    """Gene resolution priority: symbol → HGNC → alias → case-insensitive."""

    def test_exact_symbol(self, store: Store) -> None:
        assert store.resolve_gene("AARS1") == "AARS1"

    def test_case_insensitive_symbol(self, store: Store) -> None:
        assert store.resolve_gene("aars1") == "AARS1"
        assert store.resolve_gene("AaRs1") == "AARS1"

    def test_hgnc_id(self, store: Store) -> None:
        assert store.resolve_gene("HGNC:20") == "AARS1"

    def test_hgnc_id_case_insensitive(self, store: Store) -> None:
        assert store.resolve_gene("hgnc:20") == "AARS1"

    def test_erepo_only_gene_resolves(self, store: Store) -> None:
        # BRAF appears only in the ERepo fixture, not validity/dosage.
        assert store.resolve_gene("BRAF") == "BRAF"

    def test_unknown_returns_none(self, store: Store) -> None:
        assert store.resolve_gene("ZZZ_NOT_A_GENE") is None

    def test_blank_returns_none(self, store: Store) -> None:
        assert store.resolve_gene("   ") is None

    def test_whitespace_is_stripped(self, store: Store) -> None:
        assert store.resolve_gene("  AARS1  ") == "AARS1"


class TestMeta:
    """Per-domain freshness rows."""

    def test_all_domains_present(self, store: Store) -> None:
        meta = store.meta()
        assert set(meta) == {"validity", "dosage", "actionability", "erepo", "cspec"}

    def test_meta_row_fields(self, store: Store) -> None:
        row = store.meta()["validity"]
        assert row["domain"] == "validity"
        assert row["record_count"] == 5
        assert row["fetched_at"] == "2026-06-12T00:00:00.000Z"
        assert row["signal_type"]
        assert row["content_sha256"]
        assert row["source_url"].startswith("https://")

    def test_erepo_signal_is_related_version(self, store: Store) -> None:
        row = store.meta()["erepo"]
        assert row["signal_type"] == "related_version"
        assert row["signal_value"] == "2.5.6"


class TestSnapshotResolution:
    """Path handling: missing snapshot, .zst bundle, context manager."""

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SnapshotUnavailableError, match="clingen-link refresh"):
            Store(tmp_path / "does-not-exist.sqlite")

    def test_missing_bundle_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SnapshotUnavailableError, match="materialize-data"):
            Store(tmp_path / "missing.sqlite.zst")

    def test_context_manager_closes(self, test_snapshot_path: Path) -> None:
        with Store(test_snapshot_path) as s:
            assert s.resolve_gene("AARS1") == "AARS1"
        with pytest.raises(SnapshotUnavailableError):
            with s.connection():
                pass

    def test_direct_snapshot_symlink_is_rejected(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        alias = tmp_path / "alias.sqlite"
        alias.symlink_to(test_snapshot_path)
        with pytest.raises(SnapshotUnavailableError, match="symlink"):
            Store(alias)

    def test_zst_bundle_must_be_materialized_by_init_service(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        import hashlib

        import zstandard

        bundle = tmp_path / "clingen.sqlite.zst"
        data = test_snapshot_path.read_bytes()
        bundle.write_bytes(zstandard.ZstdCompressor().compress(data))
        # A sidecar is not the production trust root and the application process
        # never expands bundles; only the exact-pinned init path may do so.
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        bundle.with_suffix(".sha256").write_text(f"{digest}  {bundle.name}\n")
        with pytest.raises(SnapshotUnavailableError, match="materialized"):
            Store(bundle)

    def test_store_binds_one_selected_version_for_every_connection(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        version_a = _write_version(tmp_path, "a", test_snapshot_path, "A")
        version_b = _write_version(tmp_path, "b", test_snapshot_path, "B")
        _select(tmp_path, version_a)
        with Store(tmp_path / "current" / "clingen.sqlite", data_root=tmp_path) as store:
            _select(tmp_path, version_b)
            with store.connection() as first, store.connection() as second:
                first_marker = first.execute("SELECT marker FROM selected_version").fetchone()[0]
                second_marker = second.execute("SELECT marker FROM selected_version").fetchone()[0]

            assert (first_marker, second_marker) == ("A", "A")
            assert store.materialized_root == version_a.resolve()
        with Store(tmp_path / "current" / "clingen.sqlite", data_root=tmp_path) as restarted:
            assert restarted.materialized_root == version_b.resolve()

    def test_selector_cannot_escape_the_configured_data_root(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        external = _write_version(tmp_path, "external", test_snapshot_path, "outside")
        (data_root / "current").symlink_to(external, target_is_directory=True)

        with pytest.raises(SnapshotUnavailableError, match="outside configured data root"):
            Store(data_root / "current" / "clingen.sqlite", data_root=data_root)


class TestThreadSafety:
    """The store pools connections under a lock for concurrent reads."""

    def test_concurrent_resolves(self, store: Store) -> None:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: store.resolve_gene("AARS1"), range(32)))
        assert results == ["AARS1"] * 32
