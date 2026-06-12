"""SQLite snapshot schema (spec section 3).

The snapshot is read-only at serve time and rebuilt by the ETL. This module
holds the table + FTS5 DDL and a single :func:`create_schema` entry point. All
text-search is backed by contentless-external FTS5 virtual tables (one per
searchable domain) populated by the build writers.

Design notes:

* Every domain table carries the raw, normalized fields from the parsers. JSON
  array columns (e.g. ``haplo_pmids``, ``hgvs``) are stored as ``TEXT`` holding
  a JSON-encoded list so the serving layer can decode without a join table.
* FTS5 tables use ``content=''`` (contentless) so they store only the index;
  the build writers insert ``rowid`` + indexed columns explicitly. This keeps
  the snapshot small and avoids triggers.
* ``meta`` holds one freshness row per domain (spec section 2.2).
"""

from __future__ import annotations

import sqlite3

# ---------------------------------------------------------------------------
# Pragmas applied to a freshly created snapshot. WAL is set at serve time by
# the store; here we only need durable, fast bulk inserts.
# ---------------------------------------------------------------------------
BUILD_PRAGMAS = (
    "PRAGMA journal_mode = MEMORY;",
    "PRAGMA synchronous = OFF;",
    "PRAGMA foreign_keys = OFF;",
)

# ---------------------------------------------------------------------------
# Core tables
# ---------------------------------------------------------------------------

GENE_DDL = """
CREATE TABLE gene (
    symbol               TEXT PRIMARY KEY,
    hgnc_id              TEXT,
    name                 TEXT,
    has_validity         INTEGER NOT NULL DEFAULT 0,
    has_dosage           INTEGER NOT NULL DEFAULT 0,
    has_actionability    INTEGER NOT NULL DEFAULT 0,
    erepo_variant_count  INTEGER NOT NULL DEFAULT 0
);
"""

GENE_ALIAS_DDL = """
CREATE TABLE gene_alias (
    alias   TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    PRIMARY KEY (alias, symbol)
);
"""
GENE_ALIAS_INDEX = "CREATE INDEX idx_gene_alias_symbol ON gene_alias (symbol);"

VALIDITY_DDL = """
CREATE TABLE validity (
    symbol           TEXT NOT NULL,
    hgnc_id          TEXT,
    disease_name     TEXT,
    disease_obsolete INTEGER NOT NULL DEFAULT 0,
    mondo            TEXT,
    moi             TEXT,
    sop             TEXT,
    classification  TEXT,
    expert_panel    TEXT,
    affiliate_id    TEXT,
    perm_id         TEXT,
    report_id       TEXT,
    released        TEXT,
    classified_date TEXT
);
"""
VALIDITY_INDEX = "CREATE INDEX idx_validity_symbol ON validity (symbol);"

DOSAGE_DDL = """
CREATE TABLE dosage (
    record_type         TEXT NOT NULL,
    symbol              TEXT,
    hgnc_id             TEXT,
    isca_id             TEXT,
    cytoband            TEXT,
    grch37              TEXT,
    grch38              TEXT,
    haplo_score         TEXT,
    haplo_description   TEXT,
    haplo_disease       TEXT,
    haplo_mondo         TEXT,
    haplo_pmids         TEXT,
    triplo_score        TEXT,
    triplo_description  TEXT,
    triplo_disease      TEXT,
    triplo_mondo        TEXT,
    triplo_pmids        TEXT,
    date_last_evaluated TEXT
);
"""
DOSAGE_INDEX = "CREATE INDEX idx_dosage_symbol ON dosage (symbol);"

ACTIONABILITY_DDL = """
CREATE TABLE actionability (
    doc_id               TEXT PRIMARY KEY,
    curation_type        TEXT,
    disease              TEXT,
    modes_of_inheritance TEXT,
    last_updated         TEXT,
    last_author          TEXT,
    adult_status         TEXT,
    adult_release        TEXT,
    adult_sepio_iri      TEXT,
    pediatric_status     TEXT,
    pediatric_release    TEXT,
    pediatric_sepio_iri  TEXT,
    genes                TEXT
);
"""

EREPO_DDL = """
CREATE TABLE erepo (
    caid                  TEXT,
    clinvar_variation_id  TEXT,
    variation             TEXT,
    hgvs                  TEXT,
    gene                  TEXT,
    disease               TEXT,
    mondo                 TEXT,
    moi                   TEXT,
    assertion             TEXT,
    evidence_codes_met    TEXT,
    evidence_codes_not_met TEXT,
    summary               TEXT,
    pubmed                TEXT,
    expert_panel          TEXT,
    guideline_cspec       TEXT,
    approval_date         TEXT,
    published_date        TEXT,
    retracted             INTEGER NOT NULL DEFAULT 0,
    uuid                  TEXT,
    repo_link             TEXT
);
"""
EREPO_GENE_INDEX = "CREATE INDEX idx_erepo_gene ON erepo (gene);"
EREPO_CAID_INDEX = "CREATE INDEX idx_erepo_caid ON erepo (caid);"
EREPO_UUID_INDEX = "CREATE INDEX idx_erepo_uuid ON erepo (uuid);"

EXPERT_PANEL_DDL = """
CREATE TABLE expert_panel (
    affiliate_id    TEXT PRIMARY KEY,
    label           TEXT,
    total_curations INTEGER NOT NULL DEFAULT 0
);
"""

CSPEC_DDL = """
CREATE TABLE cspec (
    gn_id             TEXT PRIMARY KEY,
    affiliation_id    TEXT,
    affiliation_label TEXT,
    label             TEXT,
    version           TEXT,
    cspec_status      TEXT,
    current_status    TEXT,
    last_updated      TEXT,
    permalink         TEXT
);
"""
CSPEC_RULE_SET_DDL = """
CREATE TABLE cspec_rule_set (
    rule_set_id TEXT PRIMARY KEY,
    gn_id       TEXT NOT NULL
);
"""
CSPEC_RULE_SET_INDEX = "CREATE INDEX idx_cspec_rule_set_gn ON cspec_rule_set (gn_id);"
CSPEC_GENE_DDL = """
CREATE TABLE cspec_gene (
    rule_set_id TEXT NOT NULL,
    gn_id       TEXT NOT NULL,
    gene_symbol TEXT,
    hgnc_id     TEXT,
    mondo       TEXT,
    moi         TEXT
);
"""
CSPEC_GENE_GN_INDEX = "CREATE INDEX idx_cspec_gene_gn ON cspec_gene (gn_id);"
CSPEC_GENE_SYMBOL_INDEX = "CREATE INDEX idx_cspec_gene_symbol ON cspec_gene (gene_symbol);"
CSPEC_CRITERIA_DDL = """
CREATE TABLE cspec_criteria (
    criteria_id TEXT PRIMARY KEY,
    rule_set_id TEXT NOT NULL,
    gn_id       TEXT NOT NULL,
    code        TEXT NOT NULL,
    description TEXT,
    ord         INTEGER NOT NULL DEFAULT 0
);
"""
CSPEC_CRITERIA_GN_INDEX = "CREATE INDEX idx_cspec_criteria_gn ON cspec_criteria (gn_id);"
CSPEC_CRITERIA_CODE_INDEX = "CREATE INDEX idx_cspec_criteria_code ON cspec_criteria (gn_id, code);"
CSPEC_STRENGTH_DDL = """
CREATE TABLE cspec_strength (
    criteria_id    TEXT NOT NULL,
    strength_label TEXT,
    applicability  TEXT,
    description    TEXT,
    ord            INTEGER NOT NULL DEFAULT 0
);
"""
CSPEC_STRENGTH_INDEX = "CREATE INDEX idx_cspec_strength_criteria ON cspec_strength (criteria_id);"
CSPEC_FILE_DDL = """
CREATE TABLE cspec_file (
    file_uuid    TEXT NOT NULL,
    gn_id        TEXT NOT NULL,
    criteria_id  TEXT,
    filename     TEXT,
    content_type TEXT,
    size_bytes   INTEGER,
    download_url TEXT
);
"""
CSPEC_FILE_GN_INDEX = "CREATE INDEX idx_cspec_file_gn ON cspec_file (gn_id);"
# Backing row map for the mixed-entity FTS index: each cspec_fts rowid resolves
# to exactly one source entity (spec | criterion | file) via this table.
CSPEC_SEARCH_DOC_DDL = """
CREATE TABLE cspec_search_doc (
    rowid       INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL,
    gn_id       TEXT,
    criteria_id TEXT,
    file_uuid   TEXT
);
"""

META_DDL = """
CREATE TABLE meta (
    domain           TEXT PRIMARY KEY,
    source_url       TEXT,
    fetched_at       TEXT,
    signal_type      TEXT,
    signal_value     TEXT,
    content_sha256   TEXT,
    record_count     INTEGER,
    snapshot_version TEXT
);
"""

# ---------------------------------------------------------------------------
# FTS5 virtual tables (contentless). rowid links back to the source table.
# ---------------------------------------------------------------------------

VALIDITY_FTS_DDL = (
    "CREATE VIRTUAL TABLE validity_fts USING fts5("
    "disease_name, gene, content='', tokenize='unicode61');"
)
DOSAGE_FTS_DDL = (
    "CREATE VIRTUAL TABLE dosage_fts USING fts5("
    "symbol, isca_id, disease, content='', tokenize='unicode61');"
)
ACTIONABILITY_FTS_DDL = (
    "CREATE VIRTUAL TABLE actionability_fts USING fts5("
    "disease, gene, content='', tokenize='unicode61');"
)
EREPO_FTS_DDL = (
    "CREATE VIRTUAL TABLE erepo_fts USING fts5("
    "gene, disease, hgvs, content='', tokenize='unicode61');"
)
EXPERT_PANEL_FTS_DDL = (
    "CREATE VIRTUAL TABLE expert_panel_fts USING fts5(label, content='', tokenize='unicode61');"
)
CSPEC_FTS_DDL = "CREATE VIRTUAL TABLE cspec_fts USING fts5(text, content='', tokenize='unicode61');"

# Ordered DDL statements applied by create_schema().
_TABLE_STATEMENTS: tuple[str, ...] = (
    GENE_DDL,
    GENE_ALIAS_DDL,
    GENE_ALIAS_INDEX,
    VALIDITY_DDL,
    VALIDITY_INDEX,
    DOSAGE_DDL,
    DOSAGE_INDEX,
    ACTIONABILITY_DDL,
    EREPO_DDL,
    EREPO_GENE_INDEX,
    EREPO_CAID_INDEX,
    EREPO_UUID_INDEX,
    EXPERT_PANEL_DDL,
    CSPEC_DDL,
    CSPEC_RULE_SET_DDL,
    CSPEC_RULE_SET_INDEX,
    CSPEC_GENE_DDL,
    CSPEC_GENE_GN_INDEX,
    CSPEC_GENE_SYMBOL_INDEX,
    CSPEC_CRITERIA_DDL,
    CSPEC_CRITERIA_GN_INDEX,
    CSPEC_CRITERIA_CODE_INDEX,
    CSPEC_STRENGTH_DDL,
    CSPEC_STRENGTH_INDEX,
    CSPEC_FILE_DDL,
    CSPEC_FILE_GN_INDEX,
    CSPEC_SEARCH_DOC_DDL,
    META_DDL,
)

_FTS_STATEMENTS: tuple[str, ...] = (
    VALIDITY_FTS_DDL,
    DOSAGE_FTS_DDL,
    ACTIONABILITY_FTS_DDL,
    EREPO_FTS_DDL,
    EXPERT_PANEL_FTS_DDL,
    CSPEC_FTS_DDL,
)

# Names introspectable in sqlite_master after create_schema().
TABLE_NAMES: tuple[str, ...] = (
    "gene",
    "gene_alias",
    "validity",
    "dosage",
    "actionability",
    "erepo",
    "expert_panel",
    "cspec",
    "cspec_rule_set",
    "cspec_gene",
    "cspec_criteria",
    "cspec_strength",
    "cspec_file",
    "cspec_search_doc",
    "meta",
)
FTS_NAMES: tuple[str, ...] = (
    "validity_fts",
    "dosage_fts",
    "actionability_fts",
    "erepo_fts",
    "expert_panel_fts",
    "cspec_fts",
)


def create_schema(conn: sqlite3.Connection) -> None:
    """Create every table, index, and FTS5 virtual table on ``conn``.

    Idempotent only against a fresh connection: it issues plain ``CREATE``
    statements and will raise on a connection that already holds the schema.
    """
    cur = conn.cursor()
    for stmt in _TABLE_STATEMENTS:
        cur.execute(stmt)
    for stmt in _FTS_STATEMENTS:
        cur.execute(stmt)
    conn.commit()
