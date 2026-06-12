"""Read queries for the cspec domain over the bundled snapshot.

Kept separate from ``queries.py`` (which is near the 600-LOC cap). FTS hits
resolve through ``cspec_search_doc`` so a single mixed-entity index returns the
owning spec / criterion / file.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .search import Page, fts_match, paginate

_SPEC_COLS = (
    "gn_id, affiliation_id, affiliation_label, label, version, cspec_status, "
    "current_status, last_updated, permalink"
)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def get_cspec_by_gn(conn: sqlite3.Connection, gn_id: str) -> dict[str, Any] | None:
    """Return one spec header by GN id."""
    row = conn.execute(
        f"SELECT {_SPEC_COLS} FROM cspec WHERE gn_id = ?",  # noqa: S608 - fixed cols
        (gn_id,),
    ).fetchone()
    return dict(row) if row else None


def get_genes(conn: sqlite3.Connection, gn_id: str) -> list[dict[str, Any]]:
    """Return the gene/disease rows for a spec."""
    sql = (
        "SELECT rule_set_id, gn_id, gene_symbol, hgnc_id, mondo, moi "
        "FROM cspec_gene WHERE gn_id = ? ORDER BY gene_symbol"
    )
    return [dict(r) for r in _rows(conn, sql, (gn_id,))]


def get_criteria(
    conn: sqlite3.Connection, gn_id: str, *, rule_set_id: str | None = None
) -> list[dict[str, Any]]:
    """Return criteria rows for a spec (optionally one rule set), ordered."""
    sql = (
        "SELECT criteria_id, rule_set_id, gn_id, code, description, ord "
        "FROM cspec_criteria WHERE gn_id = ?"
    )
    params: list[Any] = [gn_id]
    if rule_set_id:
        sql += " AND rule_set_id = ?"
        params.append(rule_set_id)
    sql += " ORDER BY rule_set_id, ord"
    return [dict(r) for r in _rows(conn, sql, tuple(params))]


def _strengths(conn: sqlite3.Connection, criteria_id: str) -> list[dict[str, Any]]:
    sql = (
        "SELECT strength_label, applicability, description FROM cspec_strength "
        "WHERE criteria_id = ? ORDER BY ord"
    )
    return [dict(r) for r in _rows(conn, sql, (criteria_id,))]


def list_files(
    conn: sqlite3.Connection, gn_id: str, *, criteria_id: str | None = None
) -> list[dict[str, Any]]:
    """Return attachment rows for a spec or a single criterion."""
    sql = (
        "SELECT file_uuid, gn_id, criteria_id, filename, content_type, size_bytes, download_url "
        "FROM cspec_file WHERE gn_id = ?"
    )
    params: list[Any] = [gn_id]
    if criteria_id is not None:
        sql += " AND criteria_id = ?"
        params.append(criteria_id)
    return [dict(r) for r in _rows(conn, sql, tuple(params))]


def get_criterion(conn: sqlite3.Connection, criteria_id: str) -> dict[str, Any] | None:
    """Return one criterion with its strengths + attached files."""
    row = conn.execute(
        "SELECT criteria_id, rule_set_id, gn_id, code, description, ord "
        "FROM cspec_criteria WHERE criteria_id = ?",
        (criteria_id,),
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["strengths"] = _strengths(conn, criteria_id)
    out["files"] = list_files(conn, out["gn_id"], criteria_id=criteria_id)
    return out


def resolve_criterion(
    conn: sqlite3.Connection,
    gn_id: str,
    code: str,
    *,
    rule_set_id: str | None = None,
) -> list[str]:
    """Return criteria_id(s) for a (gn_id, code) — many in multi-rule-set specs."""
    sql = "SELECT criteria_id FROM cspec_criteria WHERE gn_id = ? AND code = ?"
    params: list[Any] = [gn_id, code]
    if rule_set_id:
        sql += " AND rule_set_id = ?"
        params.append(rule_set_id)
    return [r[0] for r in _rows(conn, sql, tuple(params))]


def list_cspecs(
    conn: sqlite3.Connection,
    *,
    gene: str | None = None,
    affiliation: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    """List spec headers filtered by gene/affiliation/status; paginated."""
    where: list[str] = []
    params: list[Any] = []
    if gene:
        where.append("gn_id IN (SELECT gn_id FROM cspec_gene WHERE gene_symbol = ?)")
        params.append(gene)
    if affiliation:
        where.append("affiliation_id = ?")
        params.append(affiliation)
    if status:
        where.append("cspec_status = ?")
        params.append(status)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM cspec{clause}",  # noqa: S608 - internal clause
        tuple(params),
    ).fetchone()[0]
    pg: Page = paginate(page, size)
    sql = (
        f"SELECT {_SPEC_COLS} FROM cspec{clause} "  # noqa: S608 - fixed cols/clause
        "ORDER BY gn_id LIMIT ? OFFSET ?"
    )
    rows = _rows(conn, sql, (*params, pg.size, pg.offset))
    return [dict(r) for r in rows], int(total)


def resolve_gn(
    conn: sqlite3.Connection, *, affiliation_id: str, gene: str | None = None
) -> list[str]:
    """Return published GN ids for an affiliation, narrowed by gene when given."""
    if gene:
        sql = (
            "SELECT DISTINCT c.gn_id FROM cspec c JOIN cspec_gene g ON g.gn_id = c.gn_id "
            "WHERE c.affiliation_id = ? AND g.gene_symbol = ? ORDER BY c.gn_id"
        )
        return [r[0] for r in _rows(conn, sql, (affiliation_id, gene))]
    sql = "SELECT gn_id FROM cspec WHERE affiliation_id = ? ORDER BY gn_id"
    return [r[0] for r in _rows(conn, sql, (affiliation_id,))]


def search_cspec(
    conn: sqlite3.Connection,
    *,
    text: str,
    page: int = 1,
    size: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    """FTS search across specs/criteria/filenames; resolve hits via the row map."""
    match = fts_match(text)
    if match is None:
        return [], 0
    ids = [
        int(r[0])
        for r in conn.execute(
            "SELECT rowid FROM cspec_fts WHERE cspec_fts MATCH ?", (match,)
        ).fetchall()
    ]
    if not ids:
        return [], 0
    placeholders = ",".join("?" * len(ids))
    total = len(ids)
    pg: Page = paginate(page, size)
    sql = (
        "SELECT rowid, entity_type, gn_id, criteria_id, file_uuid "  # noqa: S608 - int rowids
        f"FROM cspec_search_doc WHERE rowid IN ({placeholders}) "
        "ORDER BY rowid LIMIT ? OFFSET ?"
    )
    rows = _rows(conn, sql, (*ids, pg.size, pg.offset))
    return [dict(r) for r in rows], total
