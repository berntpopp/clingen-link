"""Snapshot builder: parse → write tables/FTS → meta → atomic swap.

:func:`build_snapshot` is the orchestrator. It accepts already-fetched raw
:class:`Sources` (so it is fully testable from in-memory fixtures, no network),
runs the pure parsers, writes every domain into a temp SQLite DB, populates the
``meta`` freshness rows, runs ``PRAGMA optimize``, and atomically ``os.replace``
es the temp file onto the destination.

The ``fetched_at`` timestamp is passed in by the caller — these functions never
call ``datetime.now()`` so the build is deterministic and testable.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import settings
from ..exceptions import SnapshotBuildError
from . import freshness, parse, schema

SNAPSHOT_VERSION = "1"

# Source URLs recorded in meta rows (for provenance surfacing in capabilities).
_SOURCE_URLS: dict[str, str] = {
    "validity": "https://search.clinicalgenome.org/api/validity",
    "dosage": "https://ftp.clinicalgenome.org/ClinGen_gene_curation_list_GRCh38.tsv",
    "actionability": "https://actionability.clinicalgenome.org/ac/api/summ/brief",
    "erepo": "https://erepo.clinicalgenome.org/evrepo/api/summary/classifications/download",
}


@dataclass
class Sources:
    """Raw, already-fetched ClinGen data feeding a snapshot build.

    Keeping this a plain container (no I/O) lets unit tests construct it from
    fixtures and exercise the full build path deterministically.
    """

    validity_rows: list[dict[str, Any]] = field(default_factory=list)
    dosage_gene_tsv: str = ""
    dosage_region_tsv: str = ""
    dosage_etags: dict[str, str] = field(default_factory=dict)
    actionability_brief: list[dict[str, Any]] = field(default_factory=list)
    erepo_tsv: str = ""
    erepo_news: list[dict[str, Any]] = field(default_factory=list)
    erepo_summary: dict[str, Any] = field(default_factory=dict)
    affiliates: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Writers — each consumes parser output and inserts into a table + its FTS.
# ---------------------------------------------------------------------------


def _write_validity(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    cur = conn.cursor()
    for rowid, row in enumerate(rows, start=1):
        cur.execute(
            "INSERT INTO validity (symbol, hgnc_id, disease_name, disease_obsolete, mondo, moi, "
            "sop, classification, expert_panel, affiliate_id, perm_id, report_id, released, "
            "classified_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["symbol"],
                row["hgnc_id"],
                row["disease_name"],
                1 if row.get("disease_obsolete") else 0,
                row["mondo"],
                row["moi"],
                row["sop"],
                row["classification"],
                row["expert_panel"],
                row["affiliate_id"],
                row["perm_id"],
                row["report_id"],
                row["released"],
                row["classified_date"],
            ),
        )
        cur.execute(
            "INSERT INTO validity_fts (rowid, disease_name, gene) VALUES (?,?,?)",
            (rowid, row["disease_name"] or "", row["symbol"] or ""),
        )
    return len(rows)


def _write_dosage(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    cur = conn.cursor()
    for rowid, row in enumerate(rows, start=1):
        cur.execute(
            "INSERT INTO dosage (record_type, symbol, hgnc_id, isca_id, cytoband, grch37, "
            "grch38, haplo_score, haplo_description, haplo_disease, haplo_mondo, haplo_pmids, "
            "triplo_score, triplo_description, triplo_disease, triplo_mondo, triplo_pmids, "
            "date_last_evaluated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["record_type"],
                row.get("symbol"),
                row.get("hgnc_id"),
                row.get("isca_id"),
                row.get("cytoband"),
                row.get("grch37"),
                row.get("grch38"),
                row.get("haplo_score"),
                row.get("haplo_description"),
                row.get("haplo_disease"),
                row.get("haplo_mondo"),
                parse.to_json(row.get("haplo_pmids") or []),
                row.get("triplo_score"),
                row.get("triplo_description"),
                row.get("triplo_disease"),
                row.get("triplo_mondo"),
                parse.to_json(row.get("triplo_pmids") or []),
                row.get("date_last_evaluated"),
            ),
        )
        disease = " ".join(v for v in (row.get("haplo_mondo"), row.get("triplo_mondo")) if v)
        cur.execute(
            "INSERT INTO dosage_fts (rowid, symbol, isca_id, disease) VALUES (?,?,?,?)",
            (rowid, row.get("symbol") or "", row.get("isca_id") or "", disease),
        )
    return len(rows)


def _write_actionability(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    cur = conn.cursor()
    for rowid, row in enumerate(rows, start=1):
        cur.execute(
            "INSERT INTO actionability (doc_id, curation_type, disease, modes_of_inheritance, "
            "last_updated, last_author, adult_status, adult_release, adult_sepio_iri, "
            "pediatric_status, pediatric_release, pediatric_sepio_iri, genes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["doc_id"],
                row["curation_type"],
                row["disease"],
                parse.to_json(row.get("modes_of_inheritance") or []),
                row["last_updated"],
                row["last_author"],
                row["adult_status"],
                row["adult_release"],
                row["adult_sepio_iri"],
                row["pediatric_status"],
                row["pediatric_release"],
                row["pediatric_sepio_iri"],
                parse.to_json(row.get("genes") or []),
            ),
        )
        cur.execute(
            "INSERT INTO actionability_fts (rowid, disease, gene) VALUES (?,?,?)",
            (rowid, row.get("disease") or "", " ".join(row.get("genes") or [])),
        )
    return len(rows)


def _write_erepo(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    cur = conn.cursor()
    for rowid, row in enumerate(rows, start=1):
        cur.execute(
            "INSERT INTO erepo (caid, clinvar_variation_id, variation, hgvs, gene, disease, "
            "mondo, moi, assertion, evidence_codes_met, evidence_codes_not_met, summary, "
            "pubmed, expert_panel, guideline_cspec, approval_date, published_date, retracted, "
            "uuid, repo_link) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["caid"],
                row["clinvar_variation_id"],
                row["variation"],
                parse.to_json(row.get("hgvs") or []),
                row["gene"],
                row["disease"],
                row["mondo"],
                row["moi"],
                row["assertion"],
                parse.to_json(row.get("evidence_codes_met") or []),
                parse.to_json(row.get("evidence_codes_not_met") or []),
                row["summary"],
                parse.to_json(row.get("pubmed") or []),
                row["expert_panel"],
                row["guideline_cspec"],
                row["approval_date"],
                row["published_date"],
                1 if row.get("retracted") else 0,
                row["uuid"],
                row["repo_link"],
            ),
        )
        cur.execute(
            "INSERT INTO erepo_fts (rowid, gene, disease, hgvs) VALUES (?,?,?,?)",
            (
                rowid,
                row.get("gene") or "",
                row.get("disease") or "",
                " ".join(row.get("hgvs") or []),
            ),
        )
    return len(rows)


def _write_genes(
    conn: sqlite3.Connection,
    genes: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
) -> int:
    cur = conn.cursor()
    for row in genes:
        cur.execute(
            "INSERT INTO gene (symbol, hgnc_id, name, has_validity, has_dosage, "
            "has_actionability, erepo_variant_count) VALUES (?,?,?,?,?,?,?)",
            (
                row["symbol"],
                row["hgnc_id"],
                row["name"],
                row["has_validity"],
                row["has_dosage"],
                row["has_actionability"],
                row["erepo_variant_count"],
            ),
        )
    cur.executemany(
        "INSERT OR IGNORE INTO gene_alias (alias, symbol) VALUES (?,?)",
        [(a["alias"], a["symbol"]) for a in aliases],
    )
    return len(genes)


def _write_expert_panels(conn: sqlite3.Connection, affiliates: list[dict[str, Any]]) -> int:
    cur = conn.cursor()
    for rowid, row in enumerate(affiliates, start=1):
        affiliate_id = str(row.get("curie") or row.get("agent") or "")
        label = row.get("label")
        total = int(row.get("count") or row.get("total_all_curations") or 0)
        cur.execute(
            "INSERT OR REPLACE INTO expert_panel (affiliate_id, label, total_curations) "
            "VALUES (?,?,?)",
            (affiliate_id, label, total),
        )
        cur.execute(
            "INSERT INTO expert_panel_fts (rowid, label) VALUES (?,?)",
            (rowid, label or ""),
        )
    return len(affiliates)


def _write_meta(
    conn: sqlite3.Connection,
    domain: str,
    signal: dict[str, Any],
    fetched_at: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (domain, source_url, fetched_at, signal_type, "
        "signal_value, content_sha256, record_count, snapshot_version) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            domain,
            _SOURCE_URLS.get(domain, ""),
            fetched_at,
            signal["signal_type"],
            signal["signal_value"],
            signal["content_sha256"],
            signal["record_count"],
            SNAPSHOT_VERSION,
        ),
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def populate(conn: sqlite3.Connection, sources: Sources, fetched_at: str) -> dict[str, int]:
    """Parse + write every domain into ``conn`` and record meta rows.

    Returns a counts dict keyed by table name. ``conn`` must already have the
    schema created.
    """
    validity = parse.parse_validity(sources.validity_rows)
    dosage = parse.parse_dosage(sources.dosage_gene_tsv, sources.dosage_region_tsv)
    actionability = parse.parse_actionability(sources.actionability_brief)
    erepo = parse.parse_erepo(sources.erepo_tsv)
    genes, aliases = parse.build_gene_index(validity, dosage, actionability, sources.erepo_summary)

    counts: dict[str, int] = {
        "validity": _write_validity(conn, validity),
        "dosage": _write_dosage(conn, dosage),
        "actionability": _write_actionability(conn, actionability),
        "erepo": _write_erepo(conn, erepo),
        "gene": _write_genes(conn, genes, aliases),
        "expert_panel": _write_expert_panels(conn, sources.affiliates),
    }
    counts["gene_alias"] = len(aliases)

    _write_meta(conn, "validity", freshness.validity_signal(validity), fetched_at)
    # The ETag set is the dosage freshness signal, but record_count must be the real row count, not
    # the number of source files (assessment H2).
    dosage_signal = freshness.dosage_signal(sources.dosage_etags)
    dosage_signal["record_count"] = len(dosage)
    _write_meta(conn, "dosage", dosage_signal, fetched_at)
    _write_meta(
        conn,
        "actionability",
        freshness.actionability_signal(sources.actionability_brief),
        fetched_at,
    )
    _write_meta(
        conn,
        "erepo",
        freshness.erepo_signal(sources.erepo_news, sources.erepo_tsv),
        fetched_at,
    )
    conn.commit()
    return counts


def build_in_memory(sources: Sources, fetched_at: str) -> sqlite3.Connection:
    """Build a complete snapshot in an in-memory connection (used by tests)."""
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    populate(conn, sources, fetched_at)
    return conn


def build_snapshot(out_path: str | Path, sources: Sources, fetched_at: str) -> dict[str, int]:
    """Build the snapshot to a temp file and atomically swap it onto ``out_path``.

    Returns the per-table row counts. Raises :class:`SnapshotBuildError` on any
    failure, leaving any pre-existing snapshot untouched.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=out_path.parent, prefix=f".{out_path.name}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        conn = sqlite3.connect(tmp_path)
        try:
            for pragma in schema.BUILD_PRAGMAS:
                conn.execute(pragma)
            schema.create_schema(conn)
            counts = populate(conn, sources, fetched_at)
            conn.execute("PRAGMA optimize;")
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_path, out_path)
        return counts
    except Exception as exc:  # pragma: no cover - re-raised as typed error
        tmp_path.unlink(missing_ok=True)
        raise SnapshotBuildError(f"snapshot build failed: {exc}") from exc


def open_readonly(path: str | Path) -> sqlite3.Connection:
    """Open ``path`` read-only (immutable) for serve-time queries."""
    uri = f"file:{Path(path).resolve()}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def default_snapshot_path() -> Path:
    """Return the configured bundled snapshot location with a ``.sqlite`` suffix."""
    configured = Path(settings.snapshot_path)
    # settings.snapshot_path defaults to the .zst bundle; the raw build target is
    # the sibling .sqlite file.
    if configured.suffix == ".zst":
        return configured.with_suffix("")
    return configured
