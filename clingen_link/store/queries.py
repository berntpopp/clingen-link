"""Pure per-domain query functions over the read-only snapshot.

Every function here takes an open :class:`sqlite3.Connection` (borrowed from a
:class:`~clingen_link.store.db.Store`) and returns raw ``dict`` rows — no
Pydantic, no caching, no business logic. JSON array columns are decoded back to
Python lists so callers never have to know they are stored as TEXT.

Search functions back text search with the contentless FTS5 tables and return a
``(rows, total)`` tuple so the service layer can paginate and build a
``truncated`` block. ``page`` is 1-based; ``size`` is clamped to a sane bound.

This module is intentionally split from the gene/freshness logic in ``db.py`` to
stay under the 600-LOC cap; FTS query escaping and pagination helpers live in
:mod:`clingen_link.store.search`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .search import Page, fts_match, paginate

# HGNC ids are unique keys, so an ``HGNC:n`` input must be matched by equality, never by a LIKE
# prefix (which would let ``HGNC:1100`` also match ``HGNC:11005`` and ``HGNC:11`` match dozens).
_HGNC_ID_RE = re.compile(r"^HGNC:\d+$", re.IGNORECASE)

# Columns selected for each domain (kept here so the SELECT and the JSON-decode
# map stay in sync). ``_JSON_COLS`` lists the TEXT columns holding JSON arrays.
_VALIDITY_COLS = (
    "symbol, hgnc_id, disease_name, mondo, moi, sop, classification, expert_panel, "
    "affiliate_id, perm_id, report_id, released, classified_date"
)
_DOSAGE_COLS = (
    "record_type, symbol, hgnc_id, isca_id, cytoband, grch37, grch38, haplo_score, "
    "haplo_description, haplo_disease, haplo_mondo, haplo_pmids, triplo_score, "
    "triplo_description, triplo_disease, triplo_mondo, triplo_pmids, date_last_evaluated"
)
_DOSAGE_JSON = ("haplo_pmids", "triplo_pmids")
_ACTIONABILITY_COLS = (
    "doc_id, curation_type, disease, modes_of_inheritance, last_updated, last_author, "
    "adult_status, adult_release, adult_sepio_iri, pediatric_status, pediatric_release, "
    "pediatric_sepio_iri, genes"
)
_ACTIONABILITY_JSON = ("modes_of_inheritance", "genes")
_EREPO_COLS = (
    "caid, clinvar_variation_id, variation, hgvs, gene, disease, mondo, moi, assertion, "
    "evidence_codes_met, evidence_codes_not_met, summary, pubmed, expert_panel, "
    "guideline_cspec, approval_date, published_date, retracted, uuid, repo_link"
)
_EREPO_JSON = ("hgvs", "evidence_codes_met", "evidence_codes_not_met", "pubmed")


def _decode(row: sqlite3.Row, json_cols: tuple[str, ...]) -> dict[str, Any]:
    """Convert a Row to a dict, decoding the named JSON-array TEXT columns."""
    out = dict(row)
    for col in json_cols:
        raw = out.get(col)
        out[col] = json.loads(raw) if isinstance(raw, str) and raw else []
    return out


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params).fetchall())


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------
def validity_for_gene(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    classification: str | None = None,
    moi: str | None = None,
) -> list[dict[str, Any]]:
    """Return validity assertions for ``symbol``, optionally filtered."""
    sql = f"SELECT {_VALIDITY_COLS} FROM validity WHERE symbol = ?"  # noqa: S608 - fixed cols
    params: list[Any] = [symbol]
    if classification:
        sql += " AND classification = ?"
        params.append(classification)
    if moi:
        sql += " AND moi = ?"
        params.append(moi)
    sql += " ORDER BY disease_name"
    return [dict(r) for r in _rows(conn, sql, tuple(params))]


def search_validity(
    conn: sqlite3.Connection,
    *,
    text: str | None = None,
    mondo: str | None = None,
    gene: str | None = None,
    expert_panel: str | None = None,
    classification: str | None = None,
    moi: str | None = None,
    page: int = 1,
    size: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    """Search validity by disease text (FTS) + structured filters; paginated.

    Returns ``(rows, total)`` where ``total`` is the unpaginated match count so
    the caller can flag truncation.
    """
    where: list[str] = []
    params: list[Any] = []
    if text:
        ids = _fts_rowids(conn, "validity_fts", text)
        if not ids:
            return [], 0
        where.append(f"rowid IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if mondo:
        where.append("mondo = ?")
        params.append(mondo)
    if gene:
        where.append("symbol = ?")
        params.append(gene)
    if expert_panel:
        where.append("expert_panel LIKE ?")
        params.append(f"%{expert_panel}%")
    if classification:
        where.append("classification = ?")
        params.append(classification)
    if moi:
        where.append("moi = ?")
        params.append(moi)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    return _paged(conn, "validity", _VALIDITY_COLS, (), clause, params, page, size, "disease_name")


# ---------------------------------------------------------------------------
# Dosage
# ---------------------------------------------------------------------------
def dosage_for_gene(conn: sqlite3.Connection, symbol: str) -> list[dict[str, Any]]:
    """Return dosage records for gene ``symbol`` (decoded PMID lists)."""
    sql = f"SELECT {_DOSAGE_COLS} FROM dosage WHERE symbol = ?"  # noqa: S608 - fixed cols
    return [_decode(r, _DOSAGE_JSON) for r in _rows(conn, sql, (symbol,))]


def search_dosage(
    conn: sqlite3.Connection,
    *,
    text: str | None = None,
    isca_id: str | None = None,
    cytoband: str | None = None,
    haplo_score: str | None = None,
    triplo_score: str | None = None,
    record_type: str | None = None,
    page: int = 1,
    size: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    """Search dosage (gene + region) by text/region/score filters; paginated."""
    where: list[str] = []
    params: list[Any] = []
    if text:
        ids = _fts_rowids(conn, "dosage_fts", text)
        if not ids:
            return [], 0
        where.append(f"rowid IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if isca_id:
        where.append("isca_id = ?")
        params.append(isca_id)
    if cytoband:
        where.append("cytoband LIKE ?")
        params.append(f"{cytoband}%")
    if haplo_score:
        where.append("haplo_score = ?")
        params.append(haplo_score)
    if triplo_score:
        where.append("triplo_score = ?")
        params.append(triplo_score)
    if record_type:
        where.append("record_type = ?")
        params.append(record_type)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    rows, total = _paged(
        conn, "dosage", _DOSAGE_COLS, _DOSAGE_JSON, clause, params, page, size, "symbol, isca_id"
    )
    return rows, total


# ---------------------------------------------------------------------------
# Actionability
# ---------------------------------------------------------------------------
def actionability_for_gene(conn: sqlite3.Connection, symbol: str) -> list[dict[str, Any]]:
    """Return actionability curations whose gene set includes ``symbol``.

    The FTS ``gene`` column indexes the space-joined gene symbols, so an exact
    token match finds every curation that lists the gene.
    """
    ids = _fts_rowids(conn, "actionability_fts", f'gene:"{symbol}"')
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    sql = (
        f"SELECT {_ACTIONABILITY_COLS} FROM actionability "  # noqa: S608 - fixed cols
        f"WHERE rowid IN ({placeholders}) ORDER BY disease"
    )
    return [_decode(r, _ACTIONABILITY_JSON) for r in _rows(conn, sql, tuple(ids))]


def search_actionability(
    conn: sqlite3.Connection,
    *,
    text: str | None = None,
    gene: str | None = None,
    page: int = 1,
    size: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    """Search actionability by disease/gene text (FTS); paginated."""
    where: list[str] = []
    params: list[Any] = []
    fts_query = text or (f'gene:"{gene}"' if gene else None)
    if fts_query:
        ids = _fts_rowids(conn, "actionability_fts", fts_query)
        if not ids:
            return [], 0
        where.append(f"rowid IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    return _paged(
        conn,
        "actionability",
        _ACTIONABILITY_COLS,
        _ACTIONABILITY_JSON,
        clause,
        params,
        page,
        size,
        "disease",
    )


# ---------------------------------------------------------------------------
# ERepo (variant pathogenicity)
# ---------------------------------------------------------------------------
def erepo_for_gene(
    conn: sqlite3.Connection,
    gene: str,
    *,
    assertion: str | None = None,
    page: int = 1,
    size: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    """Return ERepo interpretations for ``gene`` (indexed column); paginated."""
    where = ["gene = ?"]
    params: list[Any] = [gene]
    if assertion:
        where.append("assertion = ?")
        params.append(assertion)
    clause = f" WHERE {' AND '.join(where)}"
    return _paged(
        conn, "erepo", _EREPO_COLS, _EREPO_JSON, clause, params, page, size, "published_date DESC"
    )


def erepo_by_caid(conn: sqlite3.Connection, caid: str) -> dict[str, Any] | None:
    """Return the single ERepo interpretation for an Allele Registry id, or None."""
    sql = f"SELECT {_EREPO_COLS} FROM erepo WHERE caid = ? LIMIT 1"  # noqa: S608 - fixed cols
    rows = _rows(conn, sql, (caid,))
    return _decode(rows[0], _EREPO_JSON) if rows else None


def erepo_by_clinvar_id(
    conn: sqlite3.Connection, clinvar_variation_id: str
) -> dict[str, Any] | None:
    """Return the single ERepo interpretation for a ClinVar VariationID, or None."""
    sql = f"SELECT {_EREPO_COLS} FROM erepo WHERE clinvar_variation_id = ? LIMIT 1"  # noqa: S608
    rows = _rows(conn, sql, (clinvar_variation_id,))
    return _decode(rows[0], _EREPO_JSON) if rows else None


def erepo_by_hgvs(conn: sqlite3.Connection, hgvs: str) -> dict[str, Any] | None:
    """Return the first ERepo interpretation listing ``hgvs`` in its HGVS set."""
    ids = _fts_rowids(conn, "erepo_fts", f'hgvs:"{hgvs}"')
    if not ids:
        return None
    placeholders = ",".join("?" * len(ids))
    sql = (
        f"SELECT {_EREPO_COLS} FROM erepo "  # noqa: S608 - fixed cols
        f"WHERE rowid IN ({placeholders})"
    )
    for row in _rows(conn, sql, tuple(ids)):
        decoded = _decode(row, _EREPO_JSON)
        if hgvs in decoded.get("hgvs", []):
            return decoded
    return None


def search_erepo(
    conn: sqlite3.Connection,
    *,
    text: str | None = None,
    gene: str | None = None,
    mondo: str | None = None,
    expert_panel: str | None = None,
    assertion: str | None = None,
    page: int = 1,
    size: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    """Search ERepo interpretations by gene/disease/panel filters; paginated."""
    where: list[str] = []
    params: list[Any] = []
    if text:
        ids = _fts_rowids(conn, "erepo_fts", text)
        if not ids:
            return [], 0
        where.append(f"rowid IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if gene:
        where.append("gene = ?")
        params.append(gene)
    if mondo:
        where.append("mondo = ?")
        params.append(mondo)
    if expert_panel:
        where.append("expert_panel LIKE ?")
        params.append(f"%{expert_panel}%")
    if assertion:
        where.append("assertion = ?")
        params.append(assertion)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    return _paged(
        conn, "erepo", _EREPO_COLS, _EREPO_JSON, clause, params, page, size, "published_date DESC"
    )


# ---------------------------------------------------------------------------
# Gene hub + reference
# ---------------------------------------------------------------------------
def gene_summary_counts(conn: sqlite3.Connection, symbol: str) -> dict[str, Any] | None:
    """Return the gene index row + per-domain record counts for ``symbol``.

    The ``gene`` table carries availability flags + ERepo count; the validity,
    dosage, and actionability counts are computed so a single call backs
    ``get_gene_summary``.
    """
    gene = conn.execute(
        "SELECT symbol, hgnc_id, name, has_validity, has_dosage, has_actionability, "
        "erepo_variant_count FROM gene WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    if gene is None:
        return None
    out = dict(gene)
    out["validity_count"] = _count(conn, "SELECT COUNT(*) FROM validity WHERE symbol = ?", symbol)
    out["dosage_count"] = _count(conn, "SELECT COUNT(*) FROM dosage WHERE symbol = ?", symbol)
    out["actionability_count"] = len(_fts_rowids(conn, "actionability_fts", f'gene:"{symbol}"'))
    out["erepo_count"] = _count(conn, "SELECT COUNT(*) FROM erepo WHERE gene = ?", symbol)
    return out


def search_genes(conn: sqlite3.Connection, query: str, *, limit: int = 25) -> list[dict[str, Any]]:
    """Return gene index rows resolving ``query`` (symbol/alias prefix, or exact HGNC id).

    Resolution to a single canonical symbol is the store's job; this returns the candidate set for
    the ``search_genes`` tool's listing. An ``HGNC:n`` input is matched by **equality** on
    ``hgnc_id`` (plus the alias table), never by ``LIKE`` prefix, so a canonical HGNC id returns
    only its gene and a short id like ``HGNC:11`` does not pollute the candidate set (assessment H3).
    """
    cols = (
        "g.symbol, g.hgnc_id, g.name, g.has_validity, g.has_dosage, "
        "g.has_actionability, g.erepo_variant_count"
    )
    if _HGNC_ID_RE.match(query.strip()):
        hgnc = query.strip()
        rows = conn.execute(
            f"SELECT {cols} FROM gene g WHERE g.hgnc_id = ? COLLATE NOCASE "  # noqa: S608 - fixed cols
            "UNION "
            f"SELECT {cols} FROM gene g "  # noqa: S608 - fixed cols
            "JOIN gene_alias a ON a.symbol = g.symbol WHERE a.alias = ? COLLATE NOCASE "
            "ORDER BY symbol LIMIT ?",
            (hgnc, hgnc, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    like = f"{query}%"
    rows = conn.execute(
        f"SELECT {cols} FROM gene g WHERE g.symbol LIKE ? COLLATE NOCASE "  # noqa: S608 - fixed cols
        "UNION "
        f"SELECT {cols} FROM gene g "  # noqa: S608 - fixed cols
        "JOIN gene_alias a ON a.symbol = g.symbol WHERE a.alias LIKE ? COLLATE NOCASE "
        "ORDER BY symbol LIMIT ?",
        (like, like, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def expert_panels(
    conn: sqlite3.Connection, *, query: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Return expert-panel (GCEP/VCEP) rows, optionally filtered by label text."""
    if query:
        ids = _fts_rowids(conn, "expert_panel_fts", query)
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        sql = (
            "SELECT affiliate_id, label, total_curations FROM expert_panel "  # noqa: S608
            f"WHERE rowid IN ({placeholders}) ORDER BY total_curations DESC LIMIT ?"
        )
        return [dict(r) for r in _rows(conn, sql, (*ids, limit))]
    return [
        dict(r)
        for r in conn.execute(
            "SELECT affiliate_id, label, total_curations FROM expert_panel "
            "ORDER BY total_curations DESC LIMIT ?",
            (limit,),
        ).fetchall()
    ]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _count(conn: sqlite3.Connection, sql: str, *params: Any) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _fts_rowids(conn: sqlite3.Connection, table: str, text: str) -> list[int]:
    """Return rowids matching an FTS5 query, escaping bare user text safely."""
    match = fts_match(text)
    if match is None:
        return []
    sql = f"SELECT rowid FROM {table} WHERE {table} MATCH ?"  # noqa: S608 - table is internal const
    return [int(r[0]) for r in conn.execute(sql, (match,)).fetchall()]


def _paged(
    conn: sqlite3.Connection,
    table: str,
    cols: str,
    json_cols: tuple[str, ...],
    where: str,
    params: list[Any],
    page: int,
    size: int,
    order_by: str,
) -> tuple[list[dict[str, Any]], int]:
    """Run a paginated SELECT, returning ``(decoded_rows, total_match_count)``."""
    pg: Page = paginate(page, size)
    total = _count(conn, f"SELECT COUNT(*) FROM {table}{where}", *params)  # noqa: S608
    sql = (
        f"SELECT {cols} FROM {table}{where} "  # noqa: S608 - cols/table are internal consts
        f"ORDER BY {order_by} LIMIT ? OFFSET ?"
    )
    rows = _rows(conn, sql, (*params, pg.size, pg.offset))
    decoded = [_decode(r, json_cols) for r in rows] if json_cols else [dict(r) for r in rows]
    return decoded, total
