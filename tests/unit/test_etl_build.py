"""Tests for clingen_link.etl.build (writers + atomic snapshot + meta)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from clingen_link.etl import schema
from clingen_link.etl.build import (
    Sources,
    build_in_memory,
    build_snapshot,
    default_snapshot_path,
    open_readonly,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_FETCHED_AT = "2026-06-12T00:00:00+00:00"


def _load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def sources() -> Sources:
    return Sources(
        validity_rows=_load_json("validity_api_small.json")["rows"],
        dosage_gene_tsv=_read("dosage_gene_GRCh38.head.tsv"),
        dosage_region_tsv=_read("dosage_region_GRCh38.head.tsv"),
        dosage_etags={"ClinGen_gene_curation_list_GRCh38.tsv": '"abc"'},
        actionability_brief=_load_json("actionability_brief_small.json"),
        erepo_tsv=_read("erepo_bulk.head.tsv"),
        erepo_news=_load_json("erepo_news_sample.json")["data"],
        erepo_summary=_load_json("erepo_summary_sample.json"),
        affiliates=_load_json("affiliates_sample.json")["rows"],
    )


def _count(conn: sqlite3.Connection, table: str) -> int:
    # Table name is a test-controlled constant, not user input.
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def test_build_in_memory_counts(sources: Sources) -> None:
    conn = build_in_memory(sources, _FETCHED_AT)
    assert _count(conn, "validity") == 5
    assert _count(conn, "dosage") == 10
    assert _count(conn, "actionability") == 5
    assert _count(conn, "erepo") == 5
    assert _count(conn, "cspec") == 0
    assert _count(conn, "expert_panel") == 59
    assert _count(conn, "gene") > 0
    assert _count(conn, "meta") == 5


def test_build_meta_rows_complete(sources: Sources) -> None:
    conn = build_in_memory(sources, _FETCHED_AT)
    rows = conn.execute(
        "SELECT domain, signal_value, content_sha256, record_count, fetched_at FROM meta"
    ).fetchall()
    by_domain = {r[0]: r for r in rows}
    for domain in ("validity", "dosage", "actionability", "erepo"):
        assert domain in by_domain
        _, signal_value, sha, count, fetched_at = by_domain[domain]
        assert signal_value
        assert sha
        assert count is not None
        assert fetched_at == _FETCHED_AT


def test_dosage_meta_count_is_row_count_not_etag_count(sources: Sources) -> None:
    # H2 at source: the fixture has 10 dosage rows but only 1 ETag; the meta count must be 10.
    conn = build_in_memory(sources, _FETCHED_AT)
    rows = _count(conn, "dosage")
    meta_count = conn.execute("SELECT record_count FROM meta WHERE domain='dosage'").fetchone()[0]
    assert rows == 10
    assert meta_count == 10


def test_build_fts_searchable(sources: Sources) -> None:
    conn = build_in_memory(sources, _FETCHED_AT)
    rows = conn.execute(
        "SELECT v.symbol FROM validity_fts f JOIN validity v ON v.rowid = f.rowid "
        "WHERE validity_fts MATCH 'lung'"
    ).fetchall()
    assert ("ABCA3",) in rows


def test_build_dosage_pmids_stored_as_json(sources: Sources) -> None:
    conn = build_in_memory(sources, _FETCHED_AT)
    raw = conn.execute("SELECT haplo_pmids FROM dosage WHERE symbol = 'AAGAB'").fetchone()[0]
    assert json.loads(raw) == ["23064416", "23000146"]


def test_build_erepo_retracted_is_int(sources: Sources) -> None:
    conn = build_in_memory(sources, _FETCHED_AT)
    val = conn.execute("SELECT retracted FROM erepo LIMIT 1").fetchone()[0]
    assert val in (0, 1)


def test_build_snapshot_atomic_file(tmp_path: Path, sources: Sources) -> None:
    out = tmp_path / "clingen.sqlite"
    counts = build_snapshot(out, sources, _FETCHED_AT)
    assert out.exists()
    assert counts["validity"] == 5
    # No leftover temp files.
    leftovers = list(tmp_path.glob(".clingen.sqlite.*"))
    assert leftovers == []
    conn = open_readonly(out)
    assert _count(conn, "erepo") == 5


def test_open_readonly_rejects_writes(tmp_path: Path, sources: Sources) -> None:
    out = tmp_path / "clingen.sqlite"
    build_snapshot(out, sources, _FETCHED_AT)
    conn = open_readonly(out)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO validity (symbol) VALUES ('X')")


def test_default_snapshot_path_strips_zst() -> None:
    path = default_snapshot_path()
    assert path.suffix == ".sqlite"


def test_schema_pragmas_present() -> None:
    assert any("journal_mode" in p for p in schema.BUILD_PRAGMAS)
