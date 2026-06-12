# CSpec Integration (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fifth `cspec` domain to clingen-link that serves ClinGen Criteria Specification Registry data — per-VCEP ACMG/AMP criteria codes, strengths/applicability, gene/disease, and a supplementary-file catalog — FTS5-searchable and cross-linked to ERepo.

**Architecture:** Standard house spine (ETL → store → service → model → MCP tool) mirroring the four existing domains. The ETL pulls the catalog from the documented paged JSON list endpoint, fetches JSON-LD per published spec for structured criteria, and scrapes the doc-page HTML only for attachment links. Data lands in new read-only snapshot tables keyed on stable ids (`gn_id`, `rule_set_id`, numeric `criteria_id`). Four new MCP tools (`list_cspecs`, `get_cspec`, `get_cspec_criterion`, `search_cspec`) reuse the existing envelope/shaping/error infrastructure.

**Tech Stack:** Python 3.12 (strict mypy), `httpx`, `sqlite3` + FTS5, Pydantic v2, FastMCP v3, `uv`, Ruff (line length 100). Reference spec: `docs/superpowers/specs/2026-06-12-cspec-integration-design.md`.

**Key data facts (verified against the live registry):**
- Catalog: `GET https://cspec.genome.network/cspec/SequenceVariantInterpretation/id?pg=1&pgSize=250&detail=low` → `{data:[{entId:"GN001", ld:{CriteriaCode:28, RuleSet:1, ...}, ...}], metadata, status}` (235 specs).
- Per-spec JSON-LD: `GET https://cspec.genome.network/cspec/api/SequenceVariantInterpretation/id/<GN>` → `{@id, affiliation:{@id,label,url}, label, version, cspecStatus, currentStatus, lastUpdated, ruleSets:[{@id, genes:[{@id, diseases:[{@id,label}], modeOfInheritance}], criteriaCodes:[{@id, label, description, evidenceStrengths:[{label, applicability, description?}]}]}]}`.
- Inclusion gate: `cspecStatus == "Released" AND criteriaCode_count > 0`, plus the baseline doc **GN001** (null `cspecStatus`). 112/235 have 0 criteria → excluded.
- Attachments only in doc-page HTML `GET https://cspec.genome.network/cspec/ui/svi/doc/<GN>` as `…/cspec/File/id/<uuid>/data`; `HEAD` yields `content-disposition` (filename), `content-type`, `content-length`.
- `(gn_id, code)` is NOT unique: GN014 (4 rule sets) / GN016 (6) repeat codes. Numeric criterion `@id` is globally unique → use it as `criteria_id`.

---

## File Structure

**New files:**
- `clingen_link/etl/cspec_fetch.py` — httpx fetchers (catalog list, per-spec JSON-LD, doc-page HTML, file HEAD).
- `clingen_link/etl/cspec_parse.py` — pure parsers (`is_published`, `parse_spec`, gene/disease/url extraction, attachment association). Unit-tested with inline inputs.
- `clingen_link/store/cspec_queries.py` — cspec read queries (`queries.py` is 456 LOC, near the cap).
- `clingen_link/services/cspec_service.py` — `CspecService` + ERepo cross-link resolver.
- `clingen_link/mcp/tools/cspec.py` — 4 tools + `register_cspec_tools`.
- Tests: `tests/unit/etl/test_cspec_parse.py`, `tests/unit/store/test_cspec_queries.py`, `tests/unit/services/test_cspec_service.py`, `tests/unit/mcp/test_cspec_tools.py`, `tests/unit/etl/test_cspec_schema.py`.

**Modified files:**
- `clingen_link/etl/schema.py` — cspec DDL + registration.
- `clingen_link/etl/build.py` — `Sources` fields, `_write_cspec`, `populate` wiring, meta.
- `clingen_link/etl/freshness.py` — `cspec_signal`.
- `clingen_link/etl/refresh.py` — `_load_cspec`, `_DOMAINS`, `_compute_signals`.
- `clingen_link/models/models.py` — cspec Pydantic models.
- `clingen_link/models/citations.py` — `cspec_citation`.
- `clingen_link/services/aggregator.py` — wire `CspecService`.
- `clingen_link/mcp/tools/__init__.py` — register cspec tools.
- `clingen_link/mcp/shaping.py` — `_VERBOSE_FIELDS["cspec"]`.
- `clingen_link/mcp/envelope.py` — `_DOMAIN_META_KEY["cspec"]`.
- `clingen_link/mcp/resources.py` — `_DATASET_LABELS["cspec"]` + `_TOOLS`.
- `clingen_link/mcp/patterns.py` — `GN_ID_PATTERN`.
- `clingen_link/mcp/facade.py` — instruction text mentions cspec.
- `clingen_link/mcp/tools/erepo.py` — ERepo→CSpec next_commands affordance.

---

## Task 1: CSpec snapshot schema (tables + FTS + search-doc map)

**Files:**
- Modify: `clingen_link/etl/schema.py`
- Test: `tests/unit/etl/test_cspec_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/etl/test_cspec_schema.py
import sqlite3

from clingen_link.etl import schema


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()
    return {r[0] for r in rows}


def test_cspec_tables_created() -> None:
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    names = _tables(conn)
    for t in (
        "cspec",
        "cspec_rule_set",
        "cspec_gene",
        "cspec_criteria",
        "cspec_strength",
        "cspec_file",
        "cspec_fts",
        "cspec_search_doc",
    ):
        assert t in names, f"missing table {t}"
    # criteria_id is the PK; code is plain text (collides in multi-ruleset specs).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cspec_criteria)").fetchall()}
    assert {"criteria_id", "rule_set_id", "gn_id", "code", "description", "ord"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/etl/test_cspec_schema.py -v`
Expected: FAIL (cspec tables absent).

- [ ] **Step 3: Add the DDL to `schema.py`**

Add these module-level constants near the other domain DDLs (after `EXPERT_PANEL_DDL`):

```python
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
CSPEC_FTS_DDL = (
    "CREATE VIRTUAL TABLE cspec_fts USING fts5("
    "text, content='', tokenize='unicode61');"
)
```

- [ ] **Step 4: Register the new statements**

In `schema.py`, extend the three tuples. Add to `_TABLE_STATEMENTS` (before `META_DDL`):

```python
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
```

Add to `_FTS_STATEMENTS`: `CSPEC_FTS_DDL,`. Add to `TABLE_NAMES`: `"cspec", "cspec_rule_set", "cspec_gene", "cspec_criteria", "cspec_strength", "cspec_file", "cspec_search_doc",`. Add to `FTS_NAMES`: `"cspec_fts",`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/etl/test_cspec_schema.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add clingen_link/etl/schema.py tests/unit/etl/test_cspec_schema.py
git commit -m "feat(etl): add cspec snapshot schema (criteria, strengths, files, FTS row-map)"
```

---

## Task 2: Pure CSpec parser (`cspec_parse.py`)

**Files:**
- Create: `clingen_link/etl/cspec_parse.py`
- Test: `tests/unit/etl/test_cspec_parse.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/etl/test_cspec_parse.py
from clingen_link.etl import cspec_parse

_JSONLD = {
    "@id": "https://cspec.genome.network/cspec/api/SequenceVariantInterpretation/id/GN164",
    "@type": "Criteria Specification",
    "affiliation": {
        "@id": "https://cspec.genome.network/cspec/api/Organization/id/50140",
        "label": "ABCA4 Variant Curation Expert Panel ",
        "url": "https://clinicalgenome.org/affiliation/50140",
    },
    "label": "ClinGen ABCA4 Expert Panel Specifications ... Version 1.0.0",
    "version": "1.0.0",
    "cspecStatus": "Released",
    "currentStatus": "Pilot Rules In Prep",
    "lastUpdated": "2024-02-06T00:00:00.000Z",
    "ruleSets": [
        {
            "@id": "https://cspec.genome.network/cspec/api/RuleSet/id/777",
            "genes": [
                {
                    "@id": "https://www.genenames.org/tools/search/#!/?query=ABCA4",
                    "diseases": [{"@id": "http://purl.obolibrary.org/obo/MONDO_0800406",
                                  "label": "MONDO:0800406"}],
                    "modeOfInheritance": "Autosomal recessive",
                }
            ],
            "criteriaCodes": [
                {
                    "@id": "https://cspec.genome.network/cspec/api/CriteriaCode/id/538211541",
                    "label": "BS3",
                    "description": "Well-established functional studies show no damaging effect.",
                    "evidenceStrengths": [
                        {"label": "Supporting", "applicability": "Applicable",
                         "description": "See PS3/BS3 spreadsheet below."},
                        {"label": "Strong", "applicability": "Not Applicable"},
                    ],
                },
            ],
        }
    ],
}

_DOC_HTML = """
<h3>BS3</h3>
<p>guidance</p>
<a href="/cspec/File/id/abc-123/data">spreadsheet</a>
<a href="https://cspec.genome.network/cspec/File/id/def-456/data">general</a>
"""
_HEADS = {
    "https://cspec.genome.network/cspec/File/id/abc-123/data": {
        "content-disposition": 'attachment; filename=PS3-BS3-list.xlsx',
        "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content-length": "13962",
    },
    "https://cspec.genome.network/cspec/File/id/def-456/data": {
        "content-disposition": 'attachment; filename=General.pdf',
        "content-type": "application/octet-stream",
        "content-length": "100",
    },
}


def test_is_published_uses_cspec_status_not_current_status() -> None:
    assert cspec_parse.is_published(_JSONLD) is True  # Released + 1 criterion
    deleted = {**_JSONLD, "cspecStatus": "CSpec Deleted", "ruleSets": []}
    assert cspec_parse.is_published(deleted) is False
    baseline = {**_JSONLD, "cspecStatus": None,
                "@id": ".../id/GN001"}
    assert cspec_parse.is_published(baseline) is True  # GN001 baseline allowlisted


def test_parse_spec_structures_rows() -> None:
    parsed = cspec_parse.parse_spec(_JSONLD, _DOC_HTML, _HEADS)
    assert parsed.spec["gn_id"] == "GN164"
    assert parsed.spec["affiliation_id"] == "50140"
    assert parsed.spec["affiliation_label"] == "ABCA4 Variant Curation Expert Panel"
    assert parsed.spec["cspec_status"] == "Released"
    assert [rs["rule_set_id"] for rs in parsed.rule_sets] == ["777"]
    assert parsed.genes[0]["gene_symbol"] == "ABCA4"
    assert parsed.genes[0]["mondo"] == "MONDO:0800406"
    assert parsed.criteria[0]["criteria_id"] == "538211541"
    assert parsed.criteria[0]["code"] == "BS3"
    strengths = {s["strength_label"]: s["applicability"] for s in parsed.strengths}
    assert strengths == {"Supporting": "Applicable", "Strong": "Not Applicable"}
    files = {f["filename"]: f for f in parsed.files}
    assert files["PS3-BS3-list.xlsx"]["size_bytes"] == 13962
    # File under the BS3 heading associates to that criterion; the trailing one is spec-level.
    assert files["PS3-BS3-list.xlsx"]["criteria_id"] == "538211541"
    assert files["General.pdf"]["criteria_id"] in (None, "538211541")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/etl/test_cspec_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: clingen_link.etl.cspec_parse`.

- [ ] **Step 3: Implement `cspec_parse.py`**

```python
"""Pure parsers for the ClinGen Criteria Specification Registry (cspec domain).

No I/O: every function takes already-fetched JSON-LD / HTML / header dicts and
returns plain row containers, so the build path is deterministic and unit-tested
from inline inputs. Attachment links are not present in the JSON-LD; they are
harvested from the rendered doc-page HTML and associated to the nearest enclosing
criterion code (spec-level when ambiguous).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_BASE = "https://cspec.genome.network"
_FILE_RE = re.compile(r'/cspec/File/id/([0-9a-fA-F-]+)/data')
_FILENAME_RE = re.compile(r'filename="?([^"\r\n;]+)"?')
# An ACMG/AMP code token as it appears in a doc-page heading (PVS1, PS3, PM2, BA1, BS3, BP7...).
_CODE_RE = re.compile(r'\b(P(VS|S|M|P)\d|B(A|S|P)\d)[A-Za-z0-9_]*\b')
_BASELINE_GN = {"GN001"}


@dataclass
class ParsedSpec:
    """Normalized rows for one criteria specification."""

    spec: dict[str, Any]
    rule_sets: list[dict[str, Any]] = field(default_factory=list)
    genes: list[dict[str, Any]] = field(default_factory=list)
    criteria: list[dict[str, Any]] = field(default_factory=list)
    strengths: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)


def _tail_id(iri: str | None) -> str | None:
    """Return the trailing id segment of a `.../id/<val>` IRI."""
    if not iri:
        return None
    return iri.rsplit("/id/", 1)[-1] if "/id/" in iri else iri


def gn_id_of(jsonld: dict[str, Any]) -> str | None:
    """Return the GN id of a spec JSON-LD document."""
    return _tail_id(jsonld.get("@id"))


def criteria_count(jsonld: dict[str, Any]) -> int:
    """Total criteria codes across all rule sets."""
    return sum(len(rs.get("criteriaCodes", []) or []) for rs in jsonld.get("ruleSets", []) or [])


def is_published(jsonld: dict[str, Any]) -> bool:
    """Inclusion gate: Released-with-criteria, or the baseline doc GN001.

    Gates on ``cspecStatus`` (not ``currentStatus``, which drifts to e.g.
    'Pilot Rules In Prep' while a spec stays Released).
    """
    if criteria_count(jsonld) == 0:
        return False
    if (gn_id_of(jsonld) or "") in _BASELINE_GN:
        return True
    return (jsonld.get("cspecStatus") or "").strip() == "Released"


def _gene_symbol(gene: dict[str, Any]) -> str | None:
    iri = gene.get("@id") or ""
    if "query=" in iri:
        return iri.split("query=", 1)[-1].strip() or None
    return (gene.get("label") or "").strip() or None


def extract_file_urls(doc_html: str) -> list[str]:
    """Return absolute, de-duplicated attachment URLs in doc-page order."""
    out: list[str] = []
    for m in _FILE_RE.finditer(doc_html):
        url = f"{_BASE}/cspec/File/id/{m.group(1)}/data"
        if url not in out:
            out.append(url)
    return out


def _filename(headers: dict[str, str]) -> str | None:
    cd = headers.get("content-disposition") or ""
    m = _FILENAME_RE.search(cd)
    return m.group(1).strip() if m else None


def _associate_files(
    doc_html: str,
    gn_id: str,
    code_to_criteria: dict[str, str],
    heads: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Walk the doc HTML in order, tracking the current criterion heading.

    A file link is attributed to the most recent unambiguous code heading seen
    before it; spec-level (``criteria_id = None``) when none/ambiguous.
    """
    events: list[tuple[int, str, str]] = []
    for m in _CODE_RE.finditer(doc_html):
        events.append((m.start(), "code", m.group(0)))
    for m in _FILE_RE.finditer(doc_html):
        events.append((m.start(), "file", m.group(1)))
    events.sort(key=lambda e: e[0])

    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = None
    for _pos, kind, value in events:
        if kind == "code":
            current = code_to_criteria.get(value)
            continue
        if value in seen:
            continue
        seen.add(value)
        url = f"{_BASE}/cspec/File/id/{value}/data"
        headers = heads.get(url, {})
        size = headers.get("content-length")
        files.append(
            {
                "file_uuid": value,
                "gn_id": gn_id,
                "criteria_id": current,
                "filename": _filename(headers),
                "content_type": headers.get("content-type"),
                "size_bytes": int(size) if size and size.isdigit() else None,
                "download_url": url,
            }
        )
    return files


def parse_spec(
    jsonld: dict[str, Any],
    doc_html: str,
    heads: dict[str, dict[str, str]],
) -> ParsedSpec:
    """Normalize one spec's JSON-LD + doc-page attachments into row containers."""
    gn_id = gn_id_of(jsonld) or ""
    affiliation = jsonld.get("affiliation") or {}
    spec = {
        "gn_id": gn_id,
        "affiliation_id": _tail_id(affiliation.get("@id")),
        "affiliation_label": (affiliation.get("label") or "").strip() or None,
        "label": (jsonld.get("label") or "").strip() or None,
        "version": jsonld.get("version"),
        "cspec_status": jsonld.get("cspecStatus"),
        "current_status": jsonld.get("currentStatus"),
        "last_updated": jsonld.get("lastUpdated"),
        "permalink": f"{_BASE}/cspec/ui/svi/doc/{gn_id}",
    }
    parsed = ParsedSpec(spec=spec)
    code_to_criteria: dict[str, str] = {}
    for rs in jsonld.get("ruleSets", []) or []:
        rule_set_id = _tail_id(rs.get("@id")) or ""
        parsed.rule_sets.append({"rule_set_id": rule_set_id, "gn_id": gn_id})
        for gene in rs.get("genes", []) or []:
            symbol = _gene_symbol(gene)
            moi = gene.get("modeOfInheritance")
            diseases = gene.get("diseases", []) or [{}]
            for disease in diseases:
                parsed.genes.append(
                    {
                        "rule_set_id": rule_set_id,
                        "gn_id": gn_id,
                        "gene_symbol": symbol,
                        "hgnc_id": None,
                        "mondo": (disease.get("label") or None),
                        "moi": moi,
                    }
                )
        for ord_, code in enumerate(rs.get("criteriaCodes", []) or []):
            criteria_id = _tail_id(code.get("@id")) or ""
            label = code.get("label") or ""
            # Only map unambiguous code->criteria for single-rule-set specs.
            code_to_criteria.setdefault(label, criteria_id)
            if label in code_to_criteria and code_to_criteria[label] != criteria_id:
                code_to_criteria[label] = ""  # ambiguous -> spec-level
            parsed.criteria.append(
                {
                    "criteria_id": criteria_id,
                    "rule_set_id": rule_set_id,
                    "gn_id": gn_id,
                    "code": label,
                    "description": code.get("description"),
                    "ord": ord_,
                }
            )
            for s_ord, strength in enumerate(code.get("evidenceStrengths", []) or []):
                parsed.strengths.append(
                    {
                        "criteria_id": criteria_id,
                        "strength_label": strength.get("label"),
                        "applicability": strength.get("applicability"),
                        "description": strength.get("description"),
                        "ord": s_ord,
                    }
                )
    resolved = {k: v for k, v in code_to_criteria.items() if v}
    parsed.files = _associate_files(doc_html, gn_id, resolved, heads)
    return parsed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/etl/test_cspec_parse.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add clingen_link/etl/cspec_parse.py tests/unit/etl/test_cspec_parse.py
git commit -m "feat(etl): pure cspec JSON-LD + attachment parser keyed on criteria_id"
```

---

## Task 3: CSpec fetchers (`cspec_fetch.py`)

**Files:**
- Create: `clingen_link/etl/cspec_fetch.py`
- Test: `tests/unit/etl/test_cspec_fetch.py`

- [ ] **Step 1: Write the failing test** (uses `respx` per house convention)

```python
# tests/unit/etl/test_cspec_fetch.py
import httpx
import respx

from clingen_link.etl import cspec_fetch

_BASE = "https://cspec.genome.network"


@respx.mock
def test_fetch_catalog_returns_data_rows() -> None:
    respx.get(f"{_BASE}/cspec/SequenceVariantInterpretation/id").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"entId": "GN001", "ld": {"CriteriaCode": 28, "RuleSet": 1}}],
                  "status": {"code": 200}},
        )
    )
    with httpx.Client() as client:
        rows = cspec_fetch.fetch_catalog(client)
    assert rows[0]["entId"] == "GN001"


@respx.mock
def test_fetch_spec_jsonld_and_doc_page() -> None:
    respx.get(f"{_BASE}/cspec/api/SequenceVariantInterpretation/id/GN164").mock(
        return_value=httpx.Response(200, json={"@id": ".../id/GN164", "version": "1.0.0"})
    )
    respx.get(f"{_BASE}/cspec/ui/svi/doc/GN164").mock(
        return_value=httpx.Response(200, text="<html>doc</html>")
    )
    with httpx.Client() as client:
        assert cspec_fetch.fetch_spec_jsonld(client, "GN164")["version"] == "1.0.0"
        assert "doc" in cspec_fetch.fetch_doc_page(client, "GN164")


@respx.mock
def test_head_file_returns_lowercased_headers() -> None:
    url = f"{_BASE}/cspec/File/id/abc/data"
    respx.head(url).mock(
        return_value=httpx.Response(
            200, headers={"Content-Type": "application/pdf", "Content-Length": "10"}
        )
    )
    with httpx.Client() as client:
        headers = cspec_fetch.head_file(client, url)
    assert headers["content-type"] == "application/pdf"
    assert headers["content-length"] == "10"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/etl/test_cspec_fetch.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `cspec_fetch.py`** (mirror `fetch.py`'s `_get` error pattern)

```python
"""HTTP fetchers for the ClinGen Criteria Specification Registry (cspec domain).

Catalog comes from the documented paged list endpoint (non-`/api/`); structured
criteria from the per-spec JSON-LD (`/api/.../id/<GN>`); attachment links from the
rendered doc page; file metadata from a HEAD request.
"""

from __future__ import annotations

from typing import Any

import httpx

from .fetch import SourceFetchError, _get  # reuse the shared error-wrapping GET

_CSPEC_BASE = "https://cspec.genome.network"
_CATALOG_URL = f"{_CSPEC_BASE}/cspec/SequenceVariantInterpretation/id"
_TIMEOUT = httpx.Timeout(120.0, connect=30.0)


def fetch_catalog(client: httpx.Client | None = None, *, page_size: int = 250) -> list[dict[str, Any]]:
    """Return the full SVI catalog (one page; pgSize max is 250)."""
    owned = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            url = f"{_CATALOG_URL}?pg={page}&pgSize={page_size}&detail=low"
            payload = _get(client, url, "cspec").json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise SourceFetchError("cspec: unexpected catalog shape", source="cspec")
            out.extend(data)
            if len(data) < page_size:
                return out
            page += 1
    finally:
        if owned:
            client.close()


def fetch_spec_jsonld(client: httpx.Client, gn_id: str) -> dict[str, Any]:
    """Return one spec's JSON-LD document."""
    url = f"{_CSPEC_BASE}/cspec/api/SequenceVariantInterpretation/id/{gn_id}"
    payload = _get(client, url, "cspec").json()
    if not isinstance(payload, dict):
        raise SourceFetchError(f"cspec: bad JSON-LD for {gn_id}", source="cspec")
    return payload


def fetch_doc_page(client: httpx.Client, gn_id: str) -> str:
    """Return the rendered doc-page HTML (carries attachment links)."""
    url = f"{_CSPEC_BASE}/cspec/ui/svi/doc/{gn_id}"
    return _get(client, url, "cspec").text


def head_file(client: httpx.Client, url: str) -> dict[str, str]:
    """Return lower-cased response headers for an attachment URL (HEAD)."""
    resp = client.head(url, timeout=_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return {k.lower(): v for k, v in resp.headers.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/etl/test_cspec_fetch.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add clingen_link/etl/cspec_fetch.py tests/unit/etl/test_cspec_fetch.py
git commit -m "feat(etl): cspec fetchers (catalog list, JSON-LD, doc page, file HEAD)"
```

---

## Task 4: Build wiring (`Sources`, `_write_cspec`, `populate`, meta)

**Files:**
- Modify: `clingen_link/etl/build.py`, `clingen_link/etl/freshness.py`
- Test: `tests/unit/etl/test_cspec_build.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/etl/test_cspec_build.py
import sqlite3

from clingen_link.etl import build, cspec_parse, schema


def _spec_inputs():
    jsonld = {
        "@id": ".../id/GN164",
        "affiliation": {"@id": ".../id/50140", "label": "ABCA4 VCEP"},
        "label": "ABCA4 spec v1", "version": "1.0.0", "cspecStatus": "Released",
        "ruleSets": [{
            "@id": ".../id/777",
            "genes": [{"@id": ".../?query=ABCA4",
                       "diseases": [{"label": "MONDO:0800406"}], "modeOfInheritance": "AR"}],
            "criteriaCodes": [{"@id": ".../id/538211541", "label": "BS3",
                               "description": "no damaging effect",
                               "evidenceStrengths": [{"label": "Supporting",
                                                      "applicability": "Applicable"}]}],
        }],
    }
    html = '<h3>BS3</h3><a href="/cspec/File/id/abc/data">x</a>'
    heads = {"https://cspec.genome.network/cspec/File/id/abc/data":
             {"content-disposition": "filename=ABCA4-BS3.xlsx", "content-type": "x",
              "content-length": "5"}}
    return cspec_parse.parse_spec(jsonld, html, heads)


def test_write_cspec_populates_tables_and_fts() -> None:
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    count = build._write_cspec(conn, [_spec_inputs()])
    assert count == 1
    assert conn.execute("SELECT code FROM cspec_criteria").fetchone()[0] == "BS3"
    assert conn.execute("SELECT filename FROM cspec_file").fetchone()[0] == "ABCA4-BS3.xlsx"
    # FTS resolves to the criterion entity via the row map.
    rid = conn.execute(
        "SELECT rowid FROM cspec_fts WHERE cspec_fts MATCH ?", ('"BS3"',)
    ).fetchone()[0]
    doc = conn.execute(
        "SELECT entity_type, gn_id FROM cspec_search_doc WHERE rowid = ?", (rid,)
    ).fetchone()
    assert doc[1] == "GN164"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/etl/test_cspec_build.py -v`
Expected: FAIL (`build._write_cspec` missing).

- [ ] **Step 3: Add the `cspec` freshness signal to `freshness.py`**

```python
def cspec_signal(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """Freshness signal for the CSpec registry.

    Cheap (one catalog list call): the published-candidate count is the value and
    the hash covers ``(entId, criteriaCode_count, ruleSet_count)`` per spec, so
    additions, criteria changes, and rule-set changes all flip the digest without
    fetching any per-spec document.
    """
    projected: list[dict[str, str]] = []
    published = 0
    for row in catalog:
        ent_id = str(row.get("entId") or "")
        ld = row.get("ld") or {}
        cc = int(ld.get("CriteriaCode") or 0)
        rs = int(ld.get("RuleSet") or 0)
        if cc > 0:
            published += 1
        projected.append({"ent_id": ent_id, "cc": str(cc), "rs": str(rs)})
    return {
        "signal_type": "published_count",
        "signal_value": str(published),
        "content_sha256": sha256_rows(projected, ["ent_id", "cc", "rs"]),
        "record_count": published,
    }
```

- [ ] **Step 4: Wire `build.py`** — add `Sources` fields, `_write_cspec`, `populate` call, meta, source URL

In `Sources` add:

```python
    cspec_specs: list[Any] = field(default_factory=list)   # list[cspec_parse.ParsedSpec]
    cspec_catalog: list[dict[str, Any]] = field(default_factory=list)
```

Add to `_SOURCE_URLS`:

```python
    "cspec": "https://cspec.genome.network/cspec/SequenceVariantInterpretation/id",
```

Add the writer (import `cspec_parse` at top: `from . import cspec_parse`):

```python
def _write_cspec(conn: sqlite3.Connection, specs: list[cspec_parse.ParsedSpec]) -> int:
    cur = conn.cursor()
    rowid = 0
    for parsed in specs:
        s = parsed.spec
        cur.execute(
            "INSERT OR REPLACE INTO cspec (gn_id, affiliation_id, affiliation_label, label, "
            "version, cspec_status, current_status, last_updated, permalink) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (s["gn_id"], s["affiliation_id"], s["affiliation_label"], s["label"], s["version"],
             s["cspec_status"], s["current_status"], s["last_updated"], s["permalink"]),
        )
        rowid += 1
        cur.execute(
            "INSERT INTO cspec_search_doc (rowid, entity_type, gn_id, criteria_id, file_uuid) "
            "VALUES (?,?,?,?,?)",
            (rowid, "spec", s["gn_id"], None, None),
        )
        cur.execute("INSERT INTO cspec_fts (rowid, text) VALUES (?,?)",
                    (rowid, f"{s['label'] or ''} {s['affiliation_label'] or ''}"))
        for rs in parsed.rule_sets:
            cur.execute("INSERT INTO cspec_rule_set (rule_set_id, gn_id) VALUES (?,?)",
                        (rs["rule_set_id"], rs["gn_id"]))
        for g in parsed.genes:
            cur.execute(
                "INSERT INTO cspec_gene (rule_set_id, gn_id, gene_symbol, hgnc_id, mondo, moi) "
                "VALUES (?,?,?,?,?,?)",
                (g["rule_set_id"], g["gn_id"], g["gene_symbol"], g["hgnc_id"], g["mondo"], g["moi"]),
            )
        for c in parsed.criteria:
            cur.execute(
                "INSERT OR REPLACE INTO cspec_criteria (criteria_id, rule_set_id, gn_id, code, "
                "description, ord) VALUES (?,?,?,?,?,?)",
                (c["criteria_id"], c["rule_set_id"], c["gn_id"], c["code"], c["description"],
                 c["ord"]),
            )
            rowid += 1
            cur.execute(
                "INSERT INTO cspec_search_doc (rowid, entity_type, gn_id, criteria_id, file_uuid) "
                "VALUES (?,?,?,?,?)",
                (rowid, "criterion", c["gn_id"], c["criteria_id"], None),
            )
            cur.execute("INSERT INTO cspec_fts (rowid, text) VALUES (?,?)",
                        (rowid, f"{c['code'] or ''} {c['description'] or ''}"))
        for st in parsed.strengths:
            cur.execute(
                "INSERT INTO cspec_strength (criteria_id, strength_label, applicability, "
                "description, ord) VALUES (?,?,?,?,?)",
                (st["criteria_id"], st["strength_label"], st["applicability"], st["description"],
                 st["ord"]),
            )
        for f in parsed.files:
            cur.execute(
                "INSERT INTO cspec_file (file_uuid, gn_id, criteria_id, filename, content_type, "
                "size_bytes, download_url) VALUES (?,?,?,?,?,?,?)",
                (f["file_uuid"], f["gn_id"], f["criteria_id"], f["filename"], f["content_type"],
                 f["size_bytes"], f["download_url"]),
            )
            rowid += 1
            cur.execute(
                "INSERT INTO cspec_search_doc (rowid, entity_type, gn_id, criteria_id, file_uuid) "
                "VALUES (?,?,?,?,?)",
                (rowid, "file", f["gn_id"], f["criteria_id"], f["file_uuid"]),
            )
            cur.execute("INSERT INTO cspec_fts (rowid, text) VALUES (?,?)",
                        (rowid, f["filename"] or ""))
    return len(specs)
```

In `populate()`, add to the `counts` dict and write meta (import `freshness` is already present):

```python
    counts["cspec"] = _write_cspec(conn, sources.cspec_specs)
    _write_meta(conn, "cspec", freshness.cspec_signal(sources.cspec_catalog), fetched_at)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/etl/test_cspec_build.py tests/unit/etl/test_cspec_parse.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add clingen_link/etl/build.py clingen_link/etl/freshness.py tests/unit/etl/test_cspec_build.py
git commit -m "feat(etl): write cspec rows + FTS row-map; cspec freshness signal"
```

---

## Task 5: Register cspec in the refresh pipeline

**Files:**
- Modify: `clingen_link/etl/refresh.py`
- Test: `tests/unit/etl/test_cspec_refresh.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/etl/test_cspec_refresh.py
from clingen_link.etl import refresh


def test_cspec_in_domain_list() -> None:
    assert "cspec" in refresh._DOMAINS


def test_load_cspec_filters_and_parses(monkeypatch) -> None:
    from clingen_link.etl import build, cspec_fetch, cspec_parse

    catalog = [
        {"entId": "GN164", "ld": {"CriteriaCode": 1, "RuleSet": 1}},
        {"entId": "GN199", "ld": {"CriteriaCode": 0, "RuleSet": 1}},  # candidate filtered out
    ]
    jsonld = {
        "@id": ".../id/GN164", "affiliation": {"@id": ".../id/50140", "label": "ABCA4"},
        "label": "x", "version": "1.0.0", "cspecStatus": "Released",
        "ruleSets": [{"@id": ".../id/777", "genes": [],
                      "criteriaCodes": [{"@id": ".../id/1", "label": "BS3",
                                         "evidenceStrengths": []}]}],
    }
    monkeypatch.setattr(cspec_fetch, "fetch_catalog", lambda c: catalog)
    monkeypatch.setattr(cspec_fetch, "fetch_spec_jsonld", lambda c, gn: jsonld)
    monkeypatch.setattr(cspec_fetch, "fetch_doc_page", lambda c, gn: "<html></html>")
    monkeypatch.setattr(cspec_fetch, "head_file", lambda c, u: {})

    sources = build.Sources()
    refresh._load_cspec(sources, client=None)
    assert sources.cspec_catalog == catalog
    assert len(sources.cspec_specs) == 1
    assert sources.cspec_specs[0].spec["gn_id"] == "GN164"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/etl/test_cspec_refresh.py -v`
Expected: FAIL (`_load_cspec` missing; `"cspec"` not in `_DOMAINS`).

- [ ] **Step 3: Edit `refresh.py`**

Add `"cspec"` to `_DOMAINS`:

```python
_DOMAINS = ("validity", "dosage", "actionability", "erepo", "cspec")
```

Add imports at top: `from . import cspec_fetch, cspec_parse`. Add the loader:

```python
def _load_cspec(sources: Sources, client: httpx.Client | None) -> None:
    """Fetch the cspec catalog, then JSON-LD + doc page for each published spec.

    Candidate filter (cheap, from the catalog ``ld.CriteriaCode`` count) runs
    before any per-spec fetch; the ``cspecStatus`` gate runs after the JSON-LD is
    in hand so non-Released specs never trigger a doc-page/HEAD fetch.
    """
    catalog = cspec_fetch.fetch_catalog(client)
    sources.cspec_catalog = catalog
    for row in catalog:
        ld = row.get("ld") or {}
        if int(ld.get("CriteriaCode") or 0) == 0:
            continue
        gn_id = str(row.get("entId") or "")
        if not gn_id:
            continue
        jsonld = cspec_fetch.fetch_spec_jsonld(client, gn_id)
        if not cspec_parse.is_published(jsonld):
            continue
        doc_html = cspec_fetch.fetch_doc_page(client, gn_id)
        heads = {
            url: cspec_fetch.head_file(client, url)
            for url in cspec_parse.extract_file_urls(doc_html)
        }
        sources.cspec_specs.append(cspec_parse.parse_spec(jsonld, doc_html, heads))
```

Register it in `gather_sources()` (inside the `with httpx.Client(...) as client:` block):

```python
        _try(lambda: _load_cspec(sources, client), "cspec", failures)
```

In `_compute_signals()` (the helper that maps each domain to its freshness signal for `--check`), add a `cspec` entry:

```python
        "cspec": freshness.cspec_signal(sources.cspec_catalog),
```

(If `_compute_signals` calls `gather_sources()` first, the catalog is already populated; the cspec signal needs only the catalog, not the per-spec specs.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/etl/test_cspec_refresh.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add clingen_link/etl/refresh.py tests/unit/etl/test_cspec_refresh.py
git commit -m "feat(etl): register cspec domain in refresh (candidate filter + status gate)"
```

---

## Task 6: Store queries (`cspec_queries.py`)

**Files:**
- Create: `clingen_link/store/cspec_queries.py`
- Test: `tests/unit/store/test_cspec_queries.py`

- [ ] **Step 1: Write the failing test** (builds a tiny in-memory snapshot via the schema + build writer)

```python
# tests/unit/store/test_cspec_queries.py
import sqlite3

import pytest

from clingen_link.etl import build, cspec_parse, schema
from clingen_link.store import cspec_queries


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    schema.create_schema(c)
    jsonld = {
        "@id": ".../id/GN164", "affiliation": {"@id": ".../id/50140", "label": "ABCA4 VCEP"},
        "label": "ABCA4 spec", "version": "1.0.0", "cspecStatus": "Released",
        "ruleSets": [{"@id": ".../id/777",
                      "genes": [{"@id": ".../?query=ABCA4", "diseases": [{"label": "MONDO:0800406"}],
                                 "modeOfInheritance": "AR"}],
                      "criteriaCodes": [{"@id": ".../id/538211541", "label": "BS3",
                                         "description": "no damaging effect",
                                         "evidenceStrengths": [{"label": "Supporting",
                                                                "applicability": "Applicable"}]}]}],
    }
    build._write_cspec(c, [cspec_parse.parse_spec(jsonld, "", {})])
    c.commit()
    return c


def test_get_cspec_by_gn(conn) -> None:
    spec = cspec_queries.get_cspec_by_gn(conn, "GN164")
    assert spec is not None and spec["affiliation_label"] == "ABCA4 VCEP"


def test_get_criteria_and_criterion(conn) -> None:
    crit = cspec_queries.get_criteria(conn, "GN164")
    assert crit[0]["code"] == "BS3"
    one = cspec_queries.get_criterion(conn, "538211541")
    assert one is not None and one["strengths"][0]["strength_label"] == "Supporting"


def test_list_and_search(conn) -> None:
    rows, total = cspec_queries.list_cspecs(conn, gene="ABCA4")
    assert total == 1 and rows[0]["gn_id"] == "GN164"
    hits, htotal = cspec_queries.search_cspec(conn, text="damaging")
    assert htotal >= 1 and hits[0]["entity_type"] == "criterion"


def test_resolve_affiliation_gene(conn) -> None:
    assert cspec_queries.resolve_gn(conn, affiliation_id="50140", gene="ABCA4") == ["GN164"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/store/test_cspec_queries.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `cspec_queries.py`** (reuse `fts_match` from `store.search`)

```python
"""Read queries for the cspec domain over the bundled snapshot.

Kept separate from ``queries.py`` (which is near the 600-LOC cap). FTS hits
resolve through ``cspec_search_doc`` so a single mixed-entity index returns the
owning spec / criterion / file.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .search import fts_match

_SPEC_COLS = (
    "gn_id, affiliation_id, affiliation_label, label, version, cspec_status, "
    "current_status, last_updated, permalink"
)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def get_cspec_by_gn(conn: sqlite3.Connection, gn_id: str) -> dict[str, Any] | None:
    """Return one spec header by GN id."""
    row = conn.execute(
        f"SELECT {_SPEC_COLS} FROM cspec WHERE gn_id = ?", (gn_id,)  # noqa: S608 - fixed cols
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
    sql = "SELECT criteria_id, rule_set_id, gn_id, code, description, ord FROM cspec_criteria WHERE gn_id = ?"
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
        f"SELECT COUNT(*) FROM cspec{clause}", tuple(params)  # noqa: S608 - internal clause
    ).fetchone()[0]
    offset = max(0, (page - 1) * size)
    sql = (
        f"SELECT {_SPEC_COLS} FROM cspec{clause} "  # noqa: S608 - fixed cols/clause
        "ORDER BY gn_id LIMIT ? OFFSET ?"
    )
    rows = _rows(conn, sql, (*params, size, offset))
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
    offset = max(0, (page - 1) * size)
    sql = (
        "SELECT rowid, entity_type, gn_id, criteria_id, file_uuid "
        f"FROM cspec_search_doc WHERE rowid IN ({placeholders}) "  # noqa: S608 - int rowids
        "ORDER BY rowid LIMIT ? OFFSET ?"
    )
    rows = _rows(conn, sql, (*ids, size, offset))
    return [dict(r) for r in rows], total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/store/test_cspec_queries.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add clingen_link/store/cspec_queries.py tests/unit/store/test_cspec_queries.py
git commit -m "feat(store): cspec read queries with FTS row-map resolution"
```

---

## Task 7: Pydantic models + citations

**Files:**
- Modify: `clingen_link/models/models.py`, `clingen_link/models/citations.py`
- Test: `tests/unit/models/test_cspec_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/models/test_cspec_models.py
from clingen_link.models.models import CspecDetail, CspecSummary, CriteriaCode


def test_cspec_summary_citation() -> None:
    row = {
        "gn_id": "GN092", "affiliation_id": "50087",
        "affiliation_label": "ENIGMA BRCA1 and BRCA2 VCEP", "label": "ENIGMA spec",
        "version": "1.1.0", "cspec_status": "Released", "current_status": "Released",
        "last_updated": "2024-08-09T00:00:00.000Z",
        "permalink": "https://cspec.genome.network/cspec/ui/svi/doc/GN092",
    }
    m = CspecSummary.from_row(row)
    assert m.gn_id == "GN092"
    assert "ENIGMA BRCA1 and BRCA2 VCEP" in m.recommended_citation
    assert m.permalink.endswith("/doc/GN092")


def test_criteria_code_model() -> None:
    c = CriteriaCode.from_row(
        {"criteria_id": "1", "gn_id": "GN092", "code": "PVS1", "description": "null variant",
         "strengths": [{"strength_label": "Very Strong", "applicability": "Applicable",
                        "description": None}], "files": []}
    )
    assert c.code == "PVS1" and c.strengths[0].strength_label == "Very Strong"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/models/test_cspec_models.py -v`
Expected: FAIL (models missing).

- [ ] **Step 3: Add the citation builder to `citations.py`**

Add near the other permalink constants:

```python
_CSPEC_PERMALINK = "https://cspec.genome.network/cspec/ui/svi/doc/{gn_id}"
```

Add the builder:

```python
def cspec_citation(row: dict[str, Any]) -> tuple[str, str]:
    """Return ``(permalink, recommended_citation)`` for a criteria specification."""
    gn_id = _val(row, "gn_id", "")
    permalink = row.get("permalink") or (_CSPEC_PERMALINK.format(gn_id=gn_id) if gn_id else _NA)
    citation = (
        f"ClinGen Criteria Specification ({gn_id}): {_val(row, 'label')} "
        f"by {_val(row, 'affiliation_label')}, version {_val(row, 'version')}. {permalink}"
    )
    return permalink, citation
```

- [ ] **Step 4: Add the models to `models.py`**

```python
class EvidenceStrength(_Base):
    """One strength level for a criterion (applicability + optional spec text)."""

    strength_label: str | None = None
    applicability: str | None = None
    description: str | None = None


class CspecFile(_Base):
    """A supplementary guidance attachment for a spec or criterion."""

    file_uuid: str
    criteria_id: str | None = None
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    download_url: str | None = None


class CriteriaCode(_Base):
    """One ACMG/AMP criterion as specified by a VCEP."""

    criteria_id: str
    gn_id: str
    rule_set_id: str | None = None
    code: str
    description: str | None = None
    strengths: list[EvidenceStrength] = Field(default_factory=list)
    files: list[CspecFile] = Field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CriteriaCode:
        """Build from a criterion store row (strengths/files already attached)."""
        strengths = [EvidenceStrength(**_pick(s, EvidenceStrength)) for s in row.get("strengths", [])]
        files = [CspecFile(**_pick(f, CspecFile)) for f in row.get("files", [])]
        data = _pick(row, cls)
        data.update(strengths=strengths, files=files)
        return cls(**data)


class CspecGene(_Base):
    """A gene/disease covered by a spec's rule set."""

    gene_symbol: str | None = None
    hgnc_id: str | None = None
    mondo: str | None = None
    moi: str | None = None


class CspecSummary(_Base):
    """Spec header (catalog row)."""

    gn_id: str
    affiliation_id: str | None = None
    affiliation_label: str | None = None
    label: str | None = None
    version: str | None = None
    cspec_status: str | None = None
    current_status: str | None = None
    last_updated: str | None = None
    permalink: str
    recommended_citation: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CspecSummary:
        """Build a spec header with permalink + citation."""
        permalink, citation = citations.cspec_citation(row)
        return cls(permalink=permalink, recommended_citation=citation, **_pick(row, cls))


class CspecDetail(CspecSummary):
    """Spec header plus its genes, criteria, and file catalog."""

    genes: list[CspecGene] = Field(default_factory=list)
    criteria: list[CriteriaCode] = Field(default_factory=list)
    files: list[CspecFile] = Field(default_factory=list)

    @classmethod
    def assemble(
        cls,
        spec_row: dict[str, Any],
        *,
        genes: list[dict[str, Any]],
        criteria: list[dict[str, Any]],
        files: list[dict[str, Any]],
    ) -> CspecDetail:
        """Build a full detail object from store rows."""
        permalink, citation = citations.cspec_citation(spec_row)
        return cls(
            permalink=permalink,
            recommended_citation=citation,
            genes=[CspecGene(**_pick(g, CspecGene)) for g in genes],
            criteria=[CriteriaCode.from_row(c) for c in criteria],
            files=[CspecFile(**_pick(f, CspecFile)) for f in files],
            **_pick(spec_row, CspecSummary),
        )
```

Note: `_pick(spec_row, CspecSummary)` reuses the parent field set so `CspecDetail.assemble` does not duplicate the header fields. Confirm `citations` is already imported in `models.py` (it is — used by other `from_row`s).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/models/test_cspec_models.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add clingen_link/models/models.py clingen_link/models/citations.py tests/unit/models/test_cspec_models.py
git commit -m "feat(models): cspec models + criteria-specification citation"
```

---

## Task 8: CspecService + aggregator wiring

**Files:**
- Create: `clingen_link/services/cspec_service.py`
- Modify: `clingen_link/services/aggregator.py`
- Test: `tests/unit/services/test_cspec_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_cspec_service.py
import sqlite3

import pytest

from clingen_link.etl import build, cspec_parse, schema
from clingen_link.services.cspec_service import CspecService
from clingen_link.store.db import Store


@pytest.fixture
def store(tmp_path) -> Store:
    db = tmp_path / "snap.sqlite"
    conn = sqlite3.connect(db)
    schema.create_schema(conn)
    jsonld = {
        "@id": ".../id/GN092", "affiliation": {"@id": ".../id/50087", "label": "ENIGMA"},
        "label": "ENIGMA spec", "version": "1.1.0", "cspecStatus": "Released",
        "ruleSets": [{"@id": ".../id/9", "genes": [{"@id": ".../?query=BRCA1",
                      "diseases": [{"label": "MONDO:0700268"}], "modeOfInheritance": "AD"}],
                      "criteriaCodes": [{"@id": ".../id/55", "label": "PVS1",
                                         "description": "null", "evidenceStrengths": []}]}],
    }
    build._write_cspec(conn, [cspec_parse.parse_spec(jsonld, "", {})])
    conn.commit()
    conn.close()
    return Store(db)


@pytest.mark.asyncio
async def test_get_detail_and_resolve(store) -> None:
    svc = CspecService(store)
    detail = await svc.get_detail(gn_id="GN092")
    assert detail is not None and detail.criteria[0].code == "PVS1"
    assert await svc.resolve_for_erepo(affiliation_id="50087", gene="BRCA1") == ["GN092"]


@pytest.mark.asyncio
async def test_search(store) -> None:
    svc = CspecService(store)
    hits, total = await svc.search(text="ENIGMA")
    assert total >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/test_cspec_service.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `cspec_service.py`** (mirror `ValidityService`)

```python
"""CSpec service: read criteria specifications from the snapshot (cached)."""

from __future__ import annotations

from async_lru import alru_cache

from ..models.models import CspecDetail, CspecSummary
from ..store import cspec_queries
from ..store.db import Store


class CspecService:
    """Read + cache ClinGen criteria specifications from the snapshot."""

    def __init__(self, store: Store, *, cache_size: int = 256, cache_ttl_s: float = 3600) -> None:
        """Wire the store and configure the per-spec LRU+TTL cache."""
        self._store = store
        self._detail_cached = alru_cache(maxsize=cache_size, ttl=cache_ttl_s)(self._detail_impl)

    async def list_specs(
        self,
        *,
        gene: str | None = None,
        affiliation: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 25,
    ) -> tuple[list[CspecSummary], int]:
        """List spec headers; returns ``(models, total)``."""
        with self._store.connection() as conn:
            rows, total = cspec_queries.list_cspecs(
                conn, gene=gene, affiliation=affiliation, status=status, page=page, size=size
            )
        return [CspecSummary.from_row(r) for r in rows], total

    async def get_detail(self, *, gn_id: str) -> CspecDetail | None:
        """Return one spec with genes, criteria, and files (cached)."""
        return await self._detail_cached(gn_id)

    async def _detail_impl(self, gn_id: str) -> CspecDetail | None:
        with self._store.connection() as conn:
            spec = cspec_queries.get_cspec_by_gn(conn, gn_id)
            if spec is None:
                return None
            genes = cspec_queries.get_genes(conn, gn_id)
            criteria = cspec_queries.get_criteria(conn, gn_id)
            for crit in criteria:
                crit["strengths"] = cspec_queries._strengths(conn, crit["criteria_id"])
                crit["files"] = cspec_queries.list_files(conn, gn_id, criteria_id=crit["criteria_id"])
            files = cspec_queries.list_files(conn, gn_id)
        return CspecDetail.assemble(spec, genes=genes, criteria=criteria, files=files)

    async def get_criterion(self, *, criteria_id: str):
        """Return one criterion (strengths + files) or None."""
        from ..models.models import CriteriaCode

        with self._store.connection() as conn:
            row = cspec_queries.get_criterion(conn, criteria_id)
        return CriteriaCode.from_row(row) if row is not None else None

    async def resolve_criterion_ids(
        self, *, gn_id: str, code: str, rule_set_id: str | None = None
    ) -> list[str]:
        """Return criteria_id(s) for a (gn_id, code) pair."""
        with self._store.connection() as conn:
            return cspec_queries.resolve_criterion(conn, gn_id, code, rule_set_id=rule_set_id)

    async def search(
        self, *, text: str, page: int = 1, size: int = 25
    ) -> tuple[list[dict[str, object]], int]:
        """FTS search; returns ``(hit_rows, total)`` (each hit names its entity_type + ids)."""
        with self._store.connection() as conn:
            return cspec_queries.search_cspec(conn, text=text, page=page, size=size)

    async def resolve_for_erepo(self, *, affiliation_id: str, gene: str | None) -> list[str]:
        """Return GN ids for an ERepo affiliation (narrowed by gene when given)."""
        with self._store.connection() as conn:
            return cspec_queries.resolve_gn(conn, affiliation_id=affiliation_id, gene=gene)
```

(Accessing `cspec_queries._strengths` from the service is acceptable in-package; alternatively promote it to a public name. Keep it simple here.)

- [ ] **Step 4: Wire into `aggregator.py`**

Add the import: `from .cspec_service import CspecService`. In `ClingenServices.__init__`, after the `erepo` line:

```python
        self.cspec = CspecService(store, cache_size=size, cache_ttl_s=ttl_s)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/services/test_cspec_service.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add clingen_link/services/cspec_service.py clingen_link/services/aggregator.py tests/unit/services/test_cspec_service.py
git commit -m "feat(services): CspecService + aggregator wiring"
```

---

## Task 9: MCP tools (`cspec.py`) + registration

**Files:**
- Create: `clingen_link/mcp/tools/cspec.py`
- Modify: `clingen_link/mcp/tools/__init__.py`, `clingen_link/mcp/patterns.py`, `clingen_link/mcp/shaping.py`, `clingen_link/mcp/envelope.py`
- Test: `tests/unit/mcp/test_cspec_tools.py`

- [ ] **Step 1: Add supporting wiring constants**

In `patterns.py`: `GN_ID_PATTERN = r"^GN\d{1,4}$"`.

In `shaping.py` `_VERBOSE_FIELDS`, add: `"cspec": frozenset({"current_status", "affiliation_id"}),`.

In `envelope.py` `_DOMAIN_META_KEY`, add: `"cspec": "cspec",`.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/mcp/test_cspec_tools.py
import sqlite3

import pytest

from clingen_link.etl import build, cspec_parse, schema
from clingen_link.mcp.service_adapters import ClingenServices, reset_services, set_services
from clingen_link.mcp.tools import cspec as cspec_tools
from clingen_link.store.db import Store


@pytest.fixture(autouse=True)
def _services(tmp_path):
    db = tmp_path / "snap.sqlite"
    conn = sqlite3.connect(db)
    schema.create_schema(conn)
    jsonld = {
        "@id": ".../id/GN092", "affiliation": {"@id": ".../id/50087", "label": "ENIGMA"},
        "label": "ENIGMA BRCA1/2 spec", "version": "1.1.0", "cspecStatus": "Released",
        "ruleSets": [{"@id": ".../id/9", "genes": [{"@id": ".../?query=BRCA1",
                      "diseases": [{"label": "MONDO:0700268"}], "modeOfInheritance": "AD"}],
                      "criteriaCodes": [{"@id": ".../id/55", "label": "PVS1",
                                         "description": "null variant", "evidenceStrengths": []}]}],
    }
    build._write_cspec(conn, [cspec_parse.parse_spec(jsonld, "", {})])
    conn.commit()
    conn.close()
    set_services(ClingenServices(Store(db), client=None))
    yield
    reset_services()


@pytest.mark.asyncio
async def test_get_cspec_returns_detail() -> None:
    out = await cspec_tools._get_cspec_impl(gn_id="GN092", affiliation=None, gene=None,
                                            response_mode="compact")
    assert out["success"] is True
    assert out["record"]["criteria"][0]["code"] == "PVS1"
    assert out["_meta"]["unsafe_for_clinical_use"] is True


@pytest.mark.asyncio
async def test_search_cspec() -> None:
    out = await cspec_tools._search_cspec_impl(query="ENIGMA", page=1, size=10)
    assert out["total"] >= 1
```

(The tools delegate to small `_*_impl` coroutines so they are unit-testable without the FastMCP decorator; the registered tool just calls the impl inside `run_mcp_tool`.)

- [ ] **Step 3: Implement `cspec.py`**

```python
"""CSpec tools: ClinGen criteria specifications (criteria codes, strengths, files)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from clingen_link.exceptions import DataNotFoundError
from clingen_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from clingen_link.mcp.envelope import build_meta, data_version_for
from clingen_link.mcp.errors import McpErrorContext, run_mcp_tool
from clingen_link.mcp.next_commands import cmd
from clingen_link.mcp.patterns import GENE_SYMBOL_PATTERN, GN_ID_PATTERN
from clingen_link.mcp.schema_relax import relax_output_schema
from clingen_link.mcp.service_adapters import ClingenServices, get_services
from clingen_link.mcp.shaping import shape_record, shape_records, truncated_block

_RESPONSE_MODE = Any  # replaced by the shared Literal alias below

from typing import Literal  # noqa: E402

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]

_DETAIL_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "record": {"type": ["object", "null"]},
            "records": {"type": "array", "items": {"type": "object"}},
            "total": {"type": "integer"},
            "recommended_citation": {"type": ["string", "null"]},
            "_meta": {"type": "object"},
        },
    }
)


def register_cspec_tools(
    mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]
) -> None:
    """Register the four cspec tools on ``mcp``."""

    @mcp.tool(
        name="list_cspecs",
        title="List Criteria Specifications",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_DETAIL_SCHEMA,
        tags={"cspec"},
    )
    async def list_cspecs(
        gene: Annotated[str | None, Field(description="Filter to a gene symbol.",
                                          pattern=GENE_SYMBOL_PATTERN, examples=["BRCA1"])] = None,
        affiliation: Annotated[str | None, Field(description="Filter to a ClinGen affiliation id.",
                                                 examples=["50087"])] = None,
        status: Annotated[str | None, Field(description="Filter to a cspecStatus.",
                                            examples=["Released"])] = None,
        page: Annotated[int, Field(ge=1, le=1000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 25,
        response_mode: Annotated[_RESPONSE_MODE, Field(description="compact (default) trims fields.")] = "compact",
    ) -> dict[str, Any]:
        """Use this to list ClinGen criteria specifications (VCEP rule sets), filtered by gene, affiliation, or status. Paginated. Each row carries a doc permalink + recommended_citation."""
        return await run_mcp_tool(
            "list_cspecs",
            lambda: _list_cspecs_impl(gene=gene, affiliation=affiliation, status=status,
                                      page=page, size=size, response_mode=response_mode,
                                      service_factory=service_factory),
            context=McpErrorContext(tool_name="list_cspecs"),
        )

    @mcp.tool(
        name="get_cspec",
        title="Get Criteria Specification",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_DETAIL_SCHEMA,
        tags={"cspec"},
    )
    async def get_cspec(
        gn_id: Annotated[str | None, Field(description="CSpec document id.",
                                           pattern=GN_ID_PATTERN, examples=["GN092"])] = None,
        affiliation: Annotated[str | None, Field(description="ClinGen affiliation id (pair with gene).",
                                                 examples=["50087"])] = None,
        gene: Annotated[str | None, Field(description="Gene symbol (pair with affiliation, or alone).",
                                          pattern=GENE_SYMBOL_PATTERN, examples=["BRCA1"])] = None,
        response_mode: Annotated[_RESPONSE_MODE, Field(description="compact (default).")] = "compact",
    ) -> dict[str, Any]:
        """Use this to fetch one ClinGen criteria specification: its criteria codes with strengths/applicability, the genes/diseases it covers, and its supplementary-file catalog. Select by gn_id, by affiliation+gene, or by gene. Multiple matches return a list."""
        return await run_mcp_tool(
            "get_cspec",
            lambda: _get_cspec_impl(gn_id=gn_id, affiliation=affiliation, gene=gene,
                                    response_mode=response_mode, service_factory=service_factory),
            context=McpErrorContext(tool_name="get_cspec"),
        )

    @mcp.tool(
        name="get_cspec_criterion",
        title="Get Criteria Specification Criterion",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_DETAIL_SCHEMA,
        tags={"cspec"},
    )
    async def get_cspec_criterion(
        criteria_id: Annotated[str | None, Field(description="Numeric criterion id (preferred).",
                                                 examples=["538211541"])] = None,
        gn_id: Annotated[str | None, Field(description="CSpec doc id (with code).",
                                           pattern=GN_ID_PATTERN, examples=["GN092"])] = None,
        code: Annotated[str | None, Field(description="ACMG/AMP code, with gn_id.",
                                          examples=["PVS1"])] = None,
        rule_set_id: Annotated[str | None, Field(description="Disambiguates code in multi-rule-set specs.")] = None,
        response_mode: Annotated[_RESPONSE_MODE, Field(description="compact (default).")] = "compact",
    ) -> dict[str, Any]:
        """Use this to fetch one ACMG/AMP criterion's gene-specific specification: its strength levels with applicability, the spec text, and any attached guidance files. Select by criteria_id, or by gn_id + code (+ rule_set_id when the code repeats across rule sets)."""
        return await run_mcp_tool(
            "get_cspec_criterion",
            lambda: _get_criterion_impl(criteria_id=criteria_id, gn_id=gn_id, code=code,
                                        rule_set_id=rule_set_id, response_mode=response_mode,
                                        service_factory=service_factory),
            context=McpErrorContext(tool_name="get_cspec_criterion"),
        )

    @mcp.tool(
        name="search_cspec",
        title="Search Criteria Specifications",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_DETAIL_SCHEMA,
        tags={"cspec"},
    )
    async def search_cspec(
        query: Annotated[str, Field(description="Free text over specs, criteria, and filenames.",
                                    min_length=1, max_length=256, examples=["splicing BS3"])],
        page: Annotated[int, Field(ge=1, le=1000)] = 1,
        size: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> dict[str, Any]:
        """Use this to full-text search ClinGen criteria specifications. Each hit names its entity_type (spec | criterion | file) and ids so you can chain into get_cspec / get_cspec_criterion."""
        return await run_mcp_tool(
            "search_cspec",
            lambda: _search_cspec_impl(query=query, page=page, size=size,
                                       service_factory=service_factory),
            context=McpErrorContext(tool_name="search_cspec"),
        )


async def _list_cspecs_impl(*, gene, affiliation, status, page, size, response_mode,
                            service_factory=get_services) -> dict[str, Any]:
    services = service_factory()
    models, total = await services.cspec.list_specs(
        gene=gene, affiliation=affiliation, status=status, page=page, size=size
    )
    records = shape_records(models, domain="cspec", response_mode=response_mode)
    dropped = max(0, total - (page * size))
    trunc = (
        truncated_block(kind="pagination", dropped=dropped, to_restore=f"page={page + 1}",
                        to_disable="raise size",
                        filter_applied={k: v for k, v in
                                        {"gene": gene, "affiliation": affiliation,
                                         "status": status}.items() if v})
        if dropped > 0 else None
    )
    return {
        "headline": f"{total} criteria specification(s) match (page {page}).",
        "records": records,
        "total": total,
        "page": page,
        "size": size,
        "recommended_citation": models[0].recommended_citation if models else None,
        "_meta": build_meta(
            data_version=data_version_for(services.meta(), "cspec"),
            next_commands=[cmd("get_cspec", gn_id=models[0].gn_id)] if models else [],
            record_count=len(records),
            truncated=trunc,
        ),
    }


async def _get_cspec_impl(*, gn_id, affiliation, gene, response_mode,
                          service_factory=get_services) -> dict[str, Any]:
    services = service_factory()
    gn_ids: list[str] = []
    if gn_id:
        gn_ids = [gn_id]
    elif affiliation:
        gn_ids = await services.cspec.resolve_for_erepo(affiliation_id=affiliation, gene=gene)
    elif gene:
        models, _ = await services.cspec.list_specs(gene=gene, size=100)
        gn_ids = [m.gn_id for m in models]
    if not gn_ids:
        raise DataNotFoundError(
            "No criteria specification matched. Provide gn_id, affiliation+gene, or gene."
        )
    details = [d for gid in gn_ids if (d := await services.cspec.get_detail(gn_id=gid)) is not None]
    if not details:
        raise DataNotFoundError(f"Criteria specification(s) {gn_ids} not in the snapshot.")
    meta = build_meta(
        data_version=data_version_for(services.meta(), "cspec"),
        next_commands=[cmd("get_cspec_criterion", criteria_id=details[0].criteria[0].criteria_id)]
        if details[0].criteria else [],
        record_count=len(details),
    )
    if len(details) == 1:
        record = shape_record(details[0], domain="cspec", response_mode=response_mode)
        return {"headline": f"{details[0].gn_id}: {details[0].label}", "record": record,
                "total": 1, "recommended_citation": details[0].recommended_citation, "_meta": meta}
    records = shape_records(details, domain="cspec", response_mode=response_mode)
    return {"headline": f"{len(details)} specs match.", "records": records, "total": len(details),
            "recommended_citation": details[0].recommended_citation, "_meta": meta}


async def _get_criterion_impl(*, criteria_id, gn_id, code, rule_set_id, response_mode,
                              service_factory=get_services) -> dict[str, Any]:
    services = service_factory()
    if not criteria_id:
        if not (gn_id and code):
            raise DataNotFoundError("Provide criteria_id, or gn_id + code.")
        ids = await services.cspec.resolve_criterion_ids(gn_id=gn_id, code=code,
                                                          rule_set_id=rule_set_id)
        if len(ids) != 1:
            raise DataNotFoundError(
                f"{code} in {gn_id} resolved to {len(ids)} criteria; pass criteria_id or rule_set_id."
            )
        criteria_id = ids[0]
    model = await services.cspec.get_criterion(criteria_id=criteria_id)
    if model is None:
        raise DataNotFoundError(f"Criterion {criteria_id} not in the snapshot.")
    record = shape_record(model, domain="cspec", response_mode=response_mode)
    return {
        "headline": f"{model.code} ({model.gn_id})",
        "record": record,
        "total": 1,
        "recommended_citation": None,
        "_meta": build_meta(
            data_version=data_version_for(services.meta(), "cspec"),
            next_commands=[cmd("get_cspec", gn_id=model.gn_id)],
            record_count=1,
        ),
    }


async def _search_cspec_impl(*, query, page, size, service_factory=get_services) -> dict[str, Any]:
    services = service_factory()
    hits, total = await services.cspec.search(text=query, page=page, size=size)
    dropped = max(0, total - (page * size))
    nxt = []
    if hits:
        first = hits[0]
        if first.get("criteria_id"):
            nxt = [cmd("get_cspec_criterion", criteria_id=first["criteria_id"])]
        elif first.get("gn_id"):
            nxt = [cmd("get_cspec", gn_id=first["gn_id"])]
    return {
        "headline": f"{total} cspec hit(s) for {query!r} (page {page}).",
        "records": hits,
        "total": total,
        "page": page,
        "size": size,
        "_meta": build_meta(
            data_version=data_version_for(services.meta(), "cspec"),
            next_commands=nxt,
            record_count=len(hits),
            truncated=truncated_block(kind="pagination", dropped=dropped,
                                      to_restore=f"page={page + 1}", to_disable="raise size",
                                      filter_applied={"query": query}) if dropped > 0 else None,
        ),
    }
```

(Clean up the placeholder `_RESPONSE_MODE = Any` line — define the `Literal` once at the top with the other imports; it is shown twice above only to make the dependency explicit.)

- [ ] **Step 4: Register in `tools/__init__.py`**

Add `from .cspec import register_cspec_tools` and, inside `register_clingen_tools`, after `register_erepo_tools(...)`:

```python
    register_cspec_tools(mcp, service_factory=service_factory)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/mcp/test_cspec_tools.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add clingen_link/mcp/tools/cspec.py clingen_link/mcp/tools/__init__.py clingen_link/mcp/patterns.py clingen_link/mcp/shaping.py clingen_link/mcp/envelope.py tests/unit/mcp/test_cspec_tools.py
git commit -m "feat(mcp): four cspec tools (list/get/criterion/search) + envelope wiring"
```

---

## Task 10: Resources + facade discovery surface

**Files:**
- Modify: `clingen_link/mcp/resources.py`, `clingen_link/mcp/facade.py`
- Test: `tests/unit/mcp/test_cspec_resources.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/mcp/test_cspec_resources.py
from clingen_link.mcp import resources


def test_cspec_dataset_label_present() -> None:
    assert "cspec" in resources._DATASET_LABELS
    assert "Criteria Specification" in resources._DATASET_LABELS["cspec"]["label"]


def test_cspec_tools_listed() -> None:
    for tool in ("list_cspecs", "get_cspec", "get_cspec_criterion", "search_cspec"):
        assert tool in resources._TOOLS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/mcp/test_cspec_resources.py -v`
Expected: FAIL.

- [ ] **Step 3: Edit `resources.py`**

Add to `_DATASET_LABELS`:

```python
    "cspec": {
        "label": "Criteria Specifications (CSpec)",
        "question": "What ACMG/AMP rule set does a VCEP apply, and what does each criterion specify?",
    },
```

Add to `_TOOLS`:

```python
    "list_cspecs": "~2-10kB (size-dependent)",
    "get_cspec": "compact ~3-12kB",
    "get_cspec_criterion": "~1-4kB",
    "search_cspec": "~1-6kB",
```

- [ ] **Step 4: Edit `facade.py` instruction text**

In the `_INSTRUCTIONS` string, extend the canonical workflow to mention CSpec, e.g. append to the drill-down sentence: `… → get_cspec for the VCEP's ACMG/AMP rule set (criteria codes, strengths, guidance files).` and add a bullet: `Criteria specifications: list_cspecs / get_cspec / get_cspec_criterion / search_cspec expose the gene-specific ACMG/AMP rules each VCEP applies; an ERepo variant links to its CSpec via affiliation+gene.`

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/mcp/test_cspec_resources.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add clingen_link/mcp/resources.py clingen_link/mcp/facade.py tests/unit/mcp/test_cspec_resources.py
git commit -m "feat(mcp): surface cspec in capabilities/freshness resources + facade instructions"
```

---

## Task 11: ERepo → CSpec cross-link

**Files:**
- Modify: `clingen_link/mcp/tools/erepo.py`
- Test: `tests/unit/mcp/test_erepo_cspec_crosslink.py`

- [ ] **Step 1: Read `erepo.py` to locate the next_commands construction**

Run: `uv run python -c "import clingen_link.mcp.tools.erepo as m; print(m.__file__)"` then open it and find where `get_variant_interpretations` / `get_variant_interpretation` build `next_commands` in `build_meta(...)`.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/mcp/test_erepo_cspec_crosslink.py
from clingen_link.mcp.tools.erepo import cspec_next_command


def test_unique_affiliation_gene_emits_gn_id() -> None:
    cmd_ = cspec_next_command("https://cspec.clinicalgenome.org/cspec/ui/svi/affiliation/50087",
                              gene="BRCA1", resolve=lambda aff, gene: ["GN092"])
    assert cmd_ == {"tool": "get_cspec", "arguments": {"gn_id": "GN092"}}


def test_ambiguous_emits_affiliation_plus_gene() -> None:
    cmd_ = cspec_next_command("https://cspec.clinicalgenome.org/cspec/ui/svi/affiliation/50087",
                              gene="BRCA1", resolve=lambda aff, gene: ["GN092", "GN101"])
    assert cmd_ == {"tool": "get_cspec", "arguments": {"affiliation": "50087", "gene": "BRCA1"}}


def test_none_when_no_affiliation() -> None:
    assert cspec_next_command(None, gene="BRCA1", resolve=lambda a, g: []) is None
```

- [ ] **Step 3: Add the helper to `erepo.py`**

```python
import re as _re

_AFFILIATION_RE = _re.compile(r"/affiliation/(\d+)")


def cspec_next_command(
    guideline_cspec: str | None,
    *,
    gene: str | None,
    resolve: "Callable[[str, str | None], list[str]]",
) -> dict[str, Any] | None:
    """Build the ERepo→CSpec next_commands entry from a record's guideline_cspec + gene.

    Emits a precise ``{gn_id}`` when ``(affiliation, gene)`` resolves to exactly one
    published spec; otherwise ``{affiliation, gene}`` so the consumer sees candidates.
    Returns None when there is no affiliation to key on.
    """
    if not guideline_cspec:
        return None
    m = _AFFILIATION_RE.search(guideline_cspec)
    if m is None:
        return None
    affiliation = m.group(1)
    gn_ids = resolve(affiliation, gene)
    if len(gn_ids) == 1:
        return {"tool": "get_cspec", "arguments": {"gn_id": gn_ids[0]}}
    args: dict[str, Any] = {"affiliation": affiliation}
    if gene:
        args["gene"] = gene
    return {"tool": "get_cspec", "arguments": args}
```

- [ ] **Step 4: Call it where ERepo variant detail builds next_commands**

In the variant-detail tool body (where `services` and the record are in scope), resolve synchronously through the cspec service helper and append when non-None. Example insertion inside the detail tool's `call()`:

```python
        extra = cspec_next_command(
            record.guideline_cspec,
            gene=record.gene,
            resolve=lambda aff, g: services.cspec_resolve_sync(aff, g),
        )
        next_cmds = [...existing...]
        if extra is not None:
            next_cmds.append(extra)
```

Add a tiny sync bridge on `ClingenServices` (in `aggregator.py`) so the tool need not await inside the list comp:

```python
    def cspec_resolve_sync(self, affiliation_id: str, gene: str | None) -> list[str]:
        """Resolve affiliation(+gene) -> GN ids synchronously (snapshot read)."""
        with self.store.connection() as conn:
            from ..store import cspec_queries
            return cspec_queries.resolve_gn(conn, affiliation_id=affiliation_id, gene=gene)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/mcp/test_erepo_cspec_crosslink.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add clingen_link/mcp/tools/erepo.py clingen_link/services/aggregator.py tests/unit/mcp/test_erepo_cspec_crosslink.py
git commit -m "feat(mcp): ERepo variant -> CSpec next_commands (affiliation+gene, gn_id when unique)"
```

---

## Task 12: Full gate + snapshot rebuild + verification

**Files:**
- Modify: snapshot bundle `clingen_link/data/clingen.sqlite.zst` + `clingen.sqlite.sha256` (via the sanctioned refresh path; never hand-edited)
- Docs: `docs/architecture.md`, `docs/usage.md`

- [ ] **Step 1: Run the full local gate on the code**

Run: `make ci-local`
Expected: format-check, lint-ci, **lint-loc** (every new/modified module < 600 LOC), typecheck-fast (mypy strict), test-fast all PASS. If `lint-loc` flags any file, split it cohesively (e.g. move the `get_cspec` impls into a `tools/cspec_impl.py`).

- [ ] **Step 2: Build a real snapshot including cspec**

Run: `uv run clingen-link refresh --out /tmp/clingen.sqlite`
Expected: prints row counts including a non-zero `cspec` count (~120 published specs) and `cspec_*` child tables populated.

- [ ] **Step 3: Spot-check the rebuilt snapshot**

```bash
uv run python - <<'PY'
import sqlite3
c = sqlite3.connect("/tmp/clingen.sqlite")
print("specs:", c.execute("SELECT COUNT(*) FROM cspec").fetchone()[0])
print("GN092:", c.execute("SELECT label, affiliation_label FROM cspec WHERE gn_id='GN092'").fetchone())
print("GN092 criteria:", c.execute("SELECT COUNT(*) FROM cspec_criteria WHERE gn_id='GN092'").fetchone()[0])
print("multi-ruleset GN016 rule sets:", c.execute("SELECT COUNT(*) FROM cspec_rule_set WHERE gn_id='GN016'").fetchone()[0])
print("GN164 kept (Released despite currentStatus Pilot):",
      c.execute("SELECT cspec_status, current_status FROM cspec WHERE gn_id='GN164'").fetchone())
PY
```

Expected: GN092 → ENIGMA, ~28 criteria; GN016 → multiple rule sets; GN164 present with `cspec_status='Released'`.

- [ ] **Step 4: Re-bundle the snapshot (sanctioned path)**

Run the repo's compress + checksum target (mirror the existing refresh post-step):

```bash
uv run python -m zstandard --compress /tmp/clingen.sqlite -o clingen_link/data/clingen.sqlite.zst  # or the Makefile target the repo uses
sha256sum clingen_link/data/clingen.sqlite.zst | awk '{print $1}' > clingen_link/data/clingen.sqlite.sha256
```

(Use the existing repo mechanism for compression/checksum if one is defined; do not hand-edit the bundle.)

- [ ] **Step 5: Re-run the gate against the bundled snapshot**

Run: `make ci-local`
Expected: PASS, now with the cspec domain live in the bundled snapshot.

- [ ] **Step 6: Update docs**

In `docs/architecture.md`, add `cspec` to the domain list and note the two-source ETL (JSON-LD + doc-page HTML). In `docs/usage.md`, document the four tools with one example each (e.g. `get_cspec(gn_id="GN092")`). Keep it factual.

- [ ] **Step 7: Commit**

```bash
git add clingen_link/data/clingen.sqlite.zst clingen_link/data/clingen.sqlite.sha256 docs/architecture.md docs/usage.md
git commit -m "feat(data,docs): rebuild snapshot with cspec domain; document cspec tools"
```

---

## Self-Review

**Spec coverage:**
- §3.1 ETL (API catalog + JSON-LD + doc-page + status gate) → Tasks 2, 3, 5. ✓
- §3.2 tables (criteria_id/rule_set_id keys, FTS row-map) → Task 1. ✓
- §3.3 store queries (resolve/search) → Task 6. ✓
- §3.4/3.5 models + service → Tasks 7, 8. ✓
- §3.6 four tools with tightened selectors → Task 9. ✓
- §3.7 ERepo cross-link ((affiliation, gene), gn_id when unique) → Task 11. ✓
- Resources/capabilities/freshness → Task 10. ✓
- Snapshot rebuild + spot-checks (GN092/GN016/GN164) → Task 12. ✓
- Safety/citation contract → recommended_citation in every model (Task 7), `unsafe_for_clinical_use` via `run_mcp_tool` (Task 9). ✓

**Placeholder scan:** the only intentional notes are the `_RESPONSE_MODE`/`_strengths`-visibility cleanups called out inline in Tasks 9 and 8 — both have explicit resolutions. No `TODO`/`TBD` left as work.

**Type consistency:** `ParsedSpec` (Task 2) is consumed by `_write_cspec` (Task 4); store rows feed `CspecSummary.from_row` / `CspecDetail.assemble` / `CriteriaCode.from_row` (Task 7) used by `CspecService` (Task 8) and the tools (Task 9). `criteria_id`/`rule_set_id`/`gn_id` names are identical across all layers. `resolve_gn` (Task 6) is reused by both `CspecService.resolve_for_erepo` (Task 8) and `cspec_resolve_sync` (Task 11).

**Risk flagged for the implementer:** the doc-page attachment association (Task 2 `_associate_files`) is written against the verified link format but the exact heading markup wasn't fully mapped; in Task 12 capture one real doc page (e.g. GN164) and confirm the association, adjusting `_CODE_RE`/heading logic if the live DOM differs. Spec-level fallback (`criteria_id = NULL`) keeps the build correct even if association is imperfect.
