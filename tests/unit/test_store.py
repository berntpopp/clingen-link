"""Tests for the read-only SQLite store: open, resolve, freshness."""

from __future__ import annotations

from pathlib import Path

import pytest

from clingen_link.exceptions import SnapshotUnavailableError
from clingen_link.store.db import Store


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

    def test_four_domains_present(self, store: Store) -> None:
        meta = store.meta()
        assert set(meta) == {"validity", "dosage", "actionability", "erepo"}

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
        with pytest.raises(SnapshotUnavailableError, match="clingen-link refresh"):
            Store(tmp_path / "missing.sqlite.zst")

    def test_context_manager_closes(self, test_snapshot_path: Path) -> None:
        with Store(test_snapshot_path) as s:
            assert s.resolve_gene("AARS1") == "AARS1"
        with pytest.raises(SnapshotUnavailableError):
            with s.connection():
                pass

    def test_zst_bundle_decompresses_and_opens(
        self, test_snapshot_path: Path, tmp_path: Path
    ) -> None:
        import zstandard

        bundle = tmp_path / "clingen.sqlite.zst"
        data = test_snapshot_path.read_bytes()
        bundle.write_bytes(zstandard.ZstdCompressor().compress(data))
        with Store(bundle) as s:
            assert s.resolve_gene("AARS1") == "AARS1"
            assert set(s.meta()) == {"validity", "dosage", "actionability", "erepo"}


class TestThreadSafety:
    """The store pools connections under a lock for concurrent reads."""

    def test_concurrent_resolves(self, store: Store) -> None:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: store.resolve_gene("AARS1"), range(32)))
        assert results == ["AARS1"] * 32
