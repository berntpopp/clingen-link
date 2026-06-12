"""Tests for clingen_link.etl.schema (DDL + create_schema introspection)."""

from __future__ import annotations

import sqlite3

import pytest

from clingen_link.etl import schema


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    schema.create_schema(connection)
    return connection


def _names(conn: sqlite3.Connection, obj_type: str) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (obj_type,)).fetchall()
    return {row[0] for row in rows}


def test_all_tables_present(conn: sqlite3.Connection) -> None:
    tables = _names(conn, "table")
    for name in schema.TABLE_NAMES:
        assert name in tables


def test_all_fts_tables_present(conn: sqlite3.Connection) -> None:
    tables = _names(conn, "table")
    for name in schema.FTS_NAMES:
        assert name in tables


def test_meta_insert_select_roundtrip(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO meta (domain, source_url, fetched_at, signal_type, signal_value, "
        "content_sha256, record_count, snapshot_version) VALUES (?,?,?,?,?,?,?,?)",
        ("validity", "http://x", "2026-06-12T00:00:00Z", "max", "v1", "deadbeef", 3, "1"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT domain, signal_value, record_count FROM meta WHERE domain = 'validity'"
    ).fetchone()
    assert row == ("validity", "v1", 3)


def test_gene_primary_key_is_symbol(conn: sqlite3.Connection) -> None:
    cols = conn.execute("PRAGMA table_info(gene)").fetchall()
    pk_cols = [c[1] for c in cols if c[5] > 0]
    assert pk_cols == ["symbol"]


def test_fts_match_query_runs(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO validity (symbol, disease_name) VALUES ('BRCA1', 'breast cancer')")
    conn.execute(
        "INSERT INTO validity_fts (rowid, disease_name, gene) VALUES (1, 'breast cancer', 'BRCA1')"
    )
    conn.commit()
    # Contentless FTS5 stores only the index; resolve the match back to the base
    # table via rowid (the serving-layer pattern).
    rows = conn.execute(
        "SELECT v.symbol FROM validity_fts f JOIN validity v ON v.rowid = f.rowid "
        "WHERE validity_fts MATCH 'breast'"
    ).fetchall()
    assert rows == [("BRCA1",)]
