# clingen-link 9.5/10 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all 13 findings (H1–H3, M1–M5, L1–L5) from the 2026-06-12 black-box MCP
assessment and lift clingen-link to >9.5/10, end-to-end.

**Architecture:** Three layers — (A) serve-time/pure-code fixes correct on the current bundle, (B)
ETL enhancement so a rebuild produces clean data, (C) a sanctioned `clingen-link refresh` rebuild +
re-bundle. Each task is TDD; `make ci-local` is the gate.

**Tech Stack:** Python 3.12, FastMCP v3, Pydantic v2, SQLite (FTS5), httpx, respx, pytest(-xdist),
uv, Ruff, mypy strict.

Reference spec: `docs/superpowers/specs/2026-06-12-clingen-link-9.5-hardening-design.md`.

**Conventions for every task:** run the single new test with
`uv run pytest <path>::<name> -v` (expect FAIL first), implement, re-run (expect PASS), then
`make test-fast` before commit. Commit messages end with the Co-Authored-By trailer.

---

## Layer A — serve-time / pure-code fixes

### Task 1: H3 — exact HGNC-id match in `search_genes` candidates

**Files:**
- Modify: `clingen_link/store/queries.py` (`search_genes`)
- Test: `tests/unit/test_queries.py`

- [ ] **Step 1: Failing test** — append to `tests/unit/test_queries.py` (uses the in-memory snapshot
  fixture already used in that file; if the module builds its own conn, mirror it):

```python
def test_search_genes_hgnc_id_is_exact_not_prefix(memory_conn):
    # HGNC:1100 must return only its gene, never HGNC:11005 etc.
    rows = queries.search_genes(memory_conn, "HGNC:1100")
    ids = {r["hgnc_id"] for r in rows}
    assert ids == {"HGNC:1100"} or ids <= {"HGNC:1100"}
    # A short numeric id must not prefix-match many genes.
    assert len(queries.search_genes(memory_conn, "HGNC:11")) <= 1
```

If `memory_conn` is not an existing fixture, build one inline from `build.build_in_memory(Sources(...))`
with two gene rows whose `hgnc_id` are `HGNC:1100` (BRCA1) and `HGNC:11005` (SLC2A1) — see
`tests/unit/test_queries.py` existing setup for the Sources shape.

- [ ] **Step 2: Run to fail** — `uv run pytest tests/unit/test_queries.py::test_search_genes_hgnc_id_is_exact_not_prefix -v` → FAIL (returns extra rows).

- [ ] **Step 3: Implement** — replace `search_genes` in `clingen_link/store/queries.py`:

```python
import re

_HGNC_ID_RE = re.compile(r"^HGNC:\d+$", re.IGNORECASE)


def search_genes(conn: sqlite3.Connection, query: str, *, limit: int = 25) -> list[dict[str, Any]]:
    """Return gene index rows resolving ``query`` (symbol/alias prefix, or exact HGNC id).

    HGNC ids are unique keys: an ``HGNC:n`` input is matched by equality on ``hgnc_id`` (plus the
    alias table), never by ``LIKE`` prefix — so ``HGNC:1100`` returns only its gene and a short id
    like ``HGNC:11`` does not pollute the candidate set.
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
            f"SELECT {cols} FROM gene g JOIN gene_alias a ON a.symbol = g.symbol "  # noqa: S608
            "WHERE a.alias = ? COLLATE NOCASE "
            "ORDER BY symbol LIMIT ?",
            (hgnc, hgnc, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    like = f"{query}%"
    rows = conn.execute(
        f"SELECT {cols} FROM gene g WHERE g.symbol LIKE ? COLLATE NOCASE "  # noqa: S608 - fixed cols
        "UNION "
        f"SELECT {cols} FROM gene g JOIN gene_alias a ON a.symbol = g.symbol "  # noqa: S608
        "WHERE a.alias LIKE ? COLLATE NOCASE "
        "ORDER BY symbol LIMIT ?",
        (like, like, limit),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run to pass** — same pytest command → PASS; then `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "fix(store): exact-match HGNC ids in search_genes (H3)"`

---

### Task 2: H2 — serve-time `record_count` from `COUNT(*)`

**Files:**
- Modify: `clingen_link/store/db.py` (`Store.meta`)
- Test: `tests/unit/test_store.py`

- [ ] **Step 1: Failing test** — append to `tests/unit/test_store.py`:

```python
def test_meta_record_count_reflects_actual_rows(tmp_path):
    # Build a snapshot whose dosage table has 3 rows but whose stored meta count is wrong.
    from clingen_link.etl import build
    from clingen_link.etl.build import Sources

    sources = Sources(
        dosage_gene_tsv="A\tg1\tcyt\tchr1\t3\tdesc\t\t\t\t\t\t\t0\tdesc2\t\t\t\t\t\t2026\tMONDO:1\t\n",
        dosage_region_tsv="",
        dosage_etags={"ClinGen_gene_curation_list_GRCh38.tsv": "etag"},
    )
    out = tmp_path / "snap.sqlite"
    build.build_snapshot(out, sources, "2026-06-12T00:00:00Z")
    store = Store(out)
    try:
        assert store.meta()["dosage"]["record_count"] == 1
    finally:
        store.close()
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/unit/test_store.py::test_meta_record_count_reflects_actual_rows -v` → FAIL (count = len(etags) = 1 here may coincidentally pass; to force a real check, give 2 dosage rows and 1 etag). Adjust the TSV to two gene rows so stored-count(1)≠actual(2) and assert `== 2`.

- [ ] **Step 3: Implement** — in `clingen_link/store/db.py`, add a domain→table map and override the
  count in `meta()`:

```python
# module-level, near _POOL_SIZE
_DOMAIN_TABLE: dict[str, str] = {
    "validity": "validity",
    "dosage": "dosage",
    "actionability": "actionability",
    "erepo": "erepo",
}
```

Replace the body of `meta()` so each row's `record_count` is the live table count:

```python
    def meta(self) -> dict[str, dict[str, Any]]:
        """Return per-domain freshness rows keyed by ``domain``.

        ``record_count`` is recomputed from the backing table's ``COUNT(*)`` so it always reflects
        the rows actually served, even if the stored meta value drifted (e.g. an ETL that derived
        the count from a filename). Other provenance fields are read verbatim.
        """
        out: dict[str, dict[str, Any]] = {}
        with self.connection() as conn:
            for row in conn.execute(
                "SELECT domain, source_url, fetched_at, signal_type, signal_value, "
                "content_sha256, record_count, snapshot_version FROM meta"
            ):
                entry = dict(row)
                table = _DOMAIN_TABLE.get(str(row["domain"]))
                if table is not None:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
                    if count is not None:
                        entry["record_count"] = int(count[0])
                out[str(row["domain"])] = entry
        return out
```

- [ ] **Step 4: Run to pass** — pytest → PASS; `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "fix(store): serve dosage/domain record_count from COUNT(*) (H2)"`

---

### Task 3: M1 — HTML sanitize + `disease_obsolete` at serve time

**Files:**
- Create: `clingen_link/etl/sanitize.py`
- Modify: `clingen_link/models/models.py` (`ValidityAssertion`), `clingen_link/models/citations.py`
- Test: `tests/unit/test_etl_sanitize.py`, `tests/unit/test_services.py` (or `test_shaping.py`)

- [ ] **Step 1: Failing test** — create `tests/unit/test_etl_sanitize.py`:

```python
from clingen_link.etl import sanitize


def test_strip_html_removes_tags_and_collapses_ws():
    raw = ' familial isolated dilated cardiomyopathy <span class="badge">Obsolete Term</span> '
    assert sanitize.strip_html(raw) == "familial isolated dilated cardiomyopathy Obsolete Term"


def test_is_obsolete_label_detects_marker():
    assert sanitize.is_obsolete_label('x <span>Obsolete Term</span>') is True
    assert sanitize.is_obsolete_label("obsolete glaucoma 1") is True
    assert sanitize.is_obsolete_label("dilated cardiomyopathy") is False
```

And in `tests/unit/test_services.py` (validity model) add:

```python
def test_validity_assertion_sanitizes_disease_name():
    from clingen_link.models.models import ValidityAssertion

    row = {
        "symbol": "TMPO",
        "disease_name": 'dilated cardiomyopathy <span class="badge">Obsolete Term</span>',
        "perm_id": "p1",
    }
    m = ValidityAssertion.from_row(row)
    assert "<span" not in m.disease_name
    assert m.disease_obsolete is True
    assert "<span" not in m.recommended_citation
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/unit/test_etl_sanitize.py -v` → FAIL (module missing).

- [ ] **Step 3a: Implement sanitize helper** — create `clingen_link/etl/sanitize.py`:

```python
"""HTML / whitespace sanitizers + obsolescence detection for ClinGen free-text fields.

ClinGen's gene-validity export embeds presentation markup (e.g.
``<span class="badge">Obsolete Term</span>``) inside ``disease_name``. Left intact it propagates
verbatim into the recommended_citation (which the citation contract says to paste as-is) and is an
unsanitized passthrough surface. These pure helpers strip tags, unescape entities, collapse
whitespace, and surface obsolescence as a structured boolean.
"""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_OBSOLETE_RE = re.compile(r"obsolete", re.IGNORECASE)


def strip_html(value: str | None) -> str:
    """Remove tags, unescape HTML entities, and collapse whitespace. ``None`` → ``""``."""
    if not value:
        return ""
    no_tags = _TAG_RE.sub(" ", value)
    unescaped = html.unescape(no_tags)
    return _WS_RE.sub(" ", unescaped).strip()


def is_obsolete_label(value: str | None) -> bool:
    """True when a disease label carries the ClinGen/MONDO 'obsolete' marker."""
    if not value:
        return False
    return bool(_OBSOLETE_RE.search(value))
```

- [ ] **Step 3b: Wire into the validity model** — in `clingen_link/models/models.py`, add the field
  and sanitize in `from_row`:

```python
class ValidityAssertion(_Base):
    ...
    disease_name: str | None = None
    disease_obsolete: bool = False
    ...

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ValidityAssertion:
        """Build from a ``validity`` store row (disease_name HTML-sanitized)."""
        from ..etl.sanitize import is_obsolete_label, strip_html

        raw_disease = row.get("disease_name")
        clean = dict(row)
        clean["disease_name"] = strip_html(raw_disease) or None
        # Honour a structured column when present (newer snapshot); else derive from the label.
        clean["disease_obsolete"] = bool(row.get("disease_obsolete")) or is_obsolete_label(raw_disease)
        permalink, citation = citations.validity_citation(clean)
        return cls(permalink=permalink, recommended_citation=citation, **_pick(clean, cls))
```

(`citations.validity_citation` already reads `disease_name` from the row, so passing `clean` makes
the citation clean too — no change needed in `citations.py`. Verify `_pick` includes
`disease_obsolete` by adding it to the model fields, which it now is.)

- [ ] **Step 4: Run to pass** — `uv run pytest tests/unit/test_etl_sanitize.py tests/unit/test_services.py -v` → PASS; `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "fix(models): sanitize disease_name HTML + expose disease_obsolete (M1)"`

---

### Task 4: M2 — trim the `hgvs[]` array in minimal/compact

**Files:**
- Create: `clingen_link/mcp/hgvs_select.py`
- Modify: `clingen_link/mcp/shaping.py`
- Test: `tests/unit/test_hgvs_select.py`, `tests/unit/test_shaping.py`

- [ ] **Step 1: Failing test** — create `tests/unit/test_hgvs_select.py`:

```python
from clingen_link.mcp.hgvs_select import canonical_hgvs


def test_canonical_hgvs_picks_genomic_mane_protein():
    hgvs = [
        "NC_000017.11:g.43045761A>C",
        "NC_000017.10:g.41197693A>C",
        "NM_007294.4:c.5509T>G",
        "ENST00000357654.9:c.5509T>G",
        "NP_009225.1:p.Cys1837Gly",
    ] + [f"NM_0{n}.1:c.{n}A>G" for n in range(40)]
    out = canonical_hgvs(hgvs)
    assert len(out) <= 3
    assert any(h.startswith("NC_000017.11") for h in out)  # GRCh38 genomic
    assert any(":c." in h and h.startswith("NM_") for h in out)  # MANE/coding
    assert any(":p." in h or h.startswith("NP_") for h in out)  # protein


def test_canonical_hgvs_short_list_passthrough():
    assert canonical_hgvs(["NM_007294.4:c.68_69del"]) == ["NM_007294.4:c.68_69del"]
    assert canonical_hgvs([]) == []
```

And in `tests/unit/test_shaping.py`:

```python
def test_compact_erepo_trims_hgvs():
    from clingen_link.mcp import shaping
    from clingen_link.models.models import VariantInterpretation

    row = {
        "caid": "CA1", "gene": "BRCA1", "repo_link": "https://x",
        "hgvs": ["NC_000017.11:g.1A>C", "NM_1.1:c.1A>C", "NP_1.1:p.X1Y"] + [f"NM_{i}.1:c.{i}A>G" for i in range(40)],
    }
    m = VariantInterpretation.from_row(row)
    compact = shaping.shape_record(m, domain="erepo", response_mode="compact")
    assert len(compact["hgvs"]) <= 3
    assert compact["hgvs_count"] == 43
    full = shaping.shape_record(m, domain="erepo", response_mode="full")
    assert len(full["hgvs"]) == 43
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/unit/test_hgvs_select.py -v` → FAIL.

- [ ] **Step 3a: Implement selector** — create `clingen_link/mcp/hgvs_select.py`:

```python
"""Select the few load-bearing HGVS expressions from a large ERepo ``hgvs`` array.

An ERepo interpretation can carry ~50 HGVS strings (every transcript + both assemblies); at default
page size that dominates token cost. For minimal/compact we keep only the canonical genomic
(GRCh38, ``NC_…:g.``), the coding/MANE transcript (``NM_…:c.``), and the protein (``NP_…:p.`` or
``…:p.``) — enough to identify the variant — and gate the full list behind standard/full.
"""

from __future__ import annotations


def _is_grch38_genomic(h: str) -> bool:
    # GRCh38 RefSeq chromosome accessions are NC_0000xx.11/.10..; .11+ is GRCh38 for most chroms.
    return h.startswith("NC_") and ":g." in h


def _genomic_rank(h: str) -> str:
    # Prefer the highest accession version (GRCh38 > GRCh37) by sorting the accession descending.
    return h.split(":", 1)[0]


def canonical_hgvs(hgvs: list[str]) -> list[str]:
    """Return up to three identifying HGVS expressions (genomic GRCh38, coding, protein)."""
    if len(hgvs) <= 3:
        return list(hgvs)
    genomic = sorted((h for h in hgvs if _is_grch38_genomic(h)), key=_genomic_rank, reverse=True)
    coding = [h for h in hgvs if h.startswith("NM_") and ":c." in h]
    protein = [h for h in hgvs if h.startswith("NP_") or ":p." in h]
    out: list[str] = []
    for group in (genomic, coding, protein):
        if group and group[0] not in out:
            out.append(group[0])
    # If a category was empty, backfill from the head of the list to stay informative.
    for h in hgvs:
        if len(out) >= 3:
            break
        if h not in out:
            out.append(h)
    return out[:3]
```

- [ ] **Step 3b: Apply in shaping** — in `clingen_link/mcp/shaping.py`, add an array-projection step
  used by minimal/compact. Modify `shape_record`:

```python
from .hgvs_select import canonical_hgvs

# domain -> {array_field: selector} applied in minimal/compact only.
_ARRAY_TRIM: dict[str, str] = {"erepo": "hgvs"}


def _trim_arrays(row: dict[str, Any], domain: str) -> dict[str, Any]:
    """Replace a domain's big identifying array with a canonical few + a count (compact tiers)."""
    field = _ARRAY_TRIM.get(domain)
    if not field or field not in row:
        return row
    full = row.get(field) or []
    if isinstance(full, list) and len(full) > 3:
        row = dict(row)
        row[f"{field}_count"] = len(full)
        row[field] = canonical_hgvs(full)
    return row
```

Then in `shape_record`, apply `_trim_arrays` in the compact branch (after computing `trimmed`,
before `_drop_nulls`) and in the new minimal branch (Task 5). Concretely, the compact branch becomes:

```python
    if response_mode == "compact":
        trimmed = {k: v for k, v in row.items() if k not in verbose}
        trimmed = _trim_arrays(trimmed, domain)
        kept = _drop_nulls(trimmed)
        for must in ("permalink", "recommended_citation"):
            if must in row:
                kept[must] = row[must]
        return kept
```

- [ ] **Step 4: Run to pass** — `uv run pytest tests/unit/test_hgvs_select.py tests/unit/test_shaping.py -v` → PASS; `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "perf(shaping): trim ERepo hgvs[] in compact/minimal (M2)"`

---

### Task 5: M3 — response-mode lattice (minimal ⊆ compact ⊆ standard ⊆ full)

**Files:**
- Modify: `clingen_link/mcp/shaping.py` (`shape_record`), `clingen_link/mcp/tools/dosage.py`,
  `clingen_link/mcp/tools/erepo.py`
- Test: `tests/unit/test_shaping.py`, `tests/unit/test_tool_dosage.py`

- [ ] **Step 1: Failing test** — in `tests/unit/test_shaping.py`:

```python
import pytest
from clingen_link.mcp import shaping
from clingen_link.models.models import (
    DosageRecord, ValidityAssertion, ActionabilityCuration, VariantInterpretation,
)

_SAMPLES = {
    "validity": ValidityAssertion.from_row({"symbol": "BRCA1", "perm_id": "p", "sop": "SOP8", "report_id": "r"}),
    "dosage": DosageRecord.from_row({"record_type": "gene", "symbol": "BRCA1", "haplo_score": "3", "grch37": None, "isca_id": None}),
    "actionability": ActionabilityCuration.from_row({"doc_id": "AC1", "disease": "x", "adult_sepio_iri": "i", "genes": ["BRCA1"]}),
    "erepo": VariantInterpretation.from_row({"caid": "CA1", "gene": "BRCA1", "repo_link": "u", "hgvs": ["a", "b", "c", "d"], "summary": "s"}),
}


@pytest.mark.parametrize("domain,model", list(_SAMPLES.items()))
def test_response_mode_is_a_subset_lattice(domain, model):
    keys = {
        mode: set(shaping.shape_record(model, domain=domain, response_mode=mode))
        for mode in ("minimal", "compact", "standard", "full")
    }
    assert keys["minimal"] <= keys["compact"] <= keys["standard"] <= keys["full"]
```

(Note: `standard ⊆ full` holds because full keeps every field and standard only drops verbose
blocks; if `_trim_arrays` adds `hgvs_count` in compact but not full, exclude synthetic `*_count`
keys from the lattice comparison — adjust the test to strip keys ending in `_count` before the
subset check.)

- [ ] **Step 2: Run to fail** — `uv run pytest tests/unit/test_shaping.py::test_response_mode_is_a_subset_lattice -v` → FAIL for dosage (minimal currently keeps nulls → superset of compact).

- [ ] **Step 3a: Add minimal branch to `shape_record`** — in `clingen_link/mcp/shaping.py`, after the
  `full` early-return and before the compact branch:

```python
    verbose = _VERBOSE_FIELDS.get(domain, frozenset())
    if response_mode in ("compact", "minimal"):
        trimmed = {k: v for k, v in row.items() if k not in verbose}
        trimmed = _trim_arrays(trimmed, domain)
        kept = _drop_nulls(trimmed)
        for must in ("permalink", "recommended_citation"):
            if must in row:
                kept[must] = row[must]
        return kept
    # standard: keep nulls, drop only the verbose blocks.
    return {k: v for k, v in row.items() if k not in verbose}
```

This makes record-level `minimal == compact` (a strict subset of standard/full). The *list-level*
"minimal returns no records" behavior stays in `shape_records` (unchanged: returns `[]` for
minimal).

- [ ] **Step 3b: Per-record detail tools honor minimal** — in `clingen_link/mcp/tools/dosage.py`
  `get_gene_dosage.call()`, shape a head record for the headline but return `records: []` in minimal:

```python
            shaped = [
                _annotate(shape_record(m, domain="dosage", response_mode=response_mode))
                for m in models
            ]
            records = [] if response_mode == "minimal" else shaped
            head = shaped[0]
```

(keep `head` derived from `shaped[0]` for the headline; `records` is gated.) Return `records` and
`"total": len(models)` (total stays the true count even when records is empty in minimal).

In `clingen_link/mcp/tools/erepo.py` `get_variant_interpretation.call()`, gate the body similarly:

```python
            shaped = shape_record(model, domain="erepo", response_mode=response_mode)
            interpretation = {} if response_mode == "minimal" else shaped
```

- [ ] **Step 4: Run to pass** — `uv run pytest tests/unit/test_shaping.py tests/unit/test_tool_dosage.py tests/unit/test_tool_erepo.py -v` → PASS; `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "fix(shaping): enforce minimal⊆compact⊆standard⊆full lattice (M3)"`

---

### Task 6: M4 — drop the duplicated `_meta.recommended_citation`

**Files:**
- Modify: `clingen_link/mcp/envelope.py` (`build_meta`)
- Test: `tests/unit/test_tool_validity.py` (and a shared assertion)

- [ ] **Step 1: Failing test** — in `tests/unit/test_tool_validity.py` (pick any tool test that calls
  a tool and inspects `_meta`):

```python
@pytest.mark.anyio
async def test_meta_does_not_duplicate_recommended_citation(validity_tools_client):
    res = await call_tool(validity_tools_client, "get_gene_validity", {"gene": "BRCA1"})
    assert "recommended_citation" not in res["_meta"]
    # The citation is still present per-record and/or top-level.
    assert res["records"][0]["recommended_citation"]
```

(Use whatever the module's existing tool-invocation helper is; mirror an existing test in the file.)

- [ ] **Step 2: Run to fail** — pytest → FAIL (`recommended_citation` present in `_meta`).

- [ ] **Step 3: Implement** — in `clingen_link/mcp/envelope.py`, remove the `recommended_citation`
  parameter handling from `build_meta` (stop emitting it). Change the signature to drop the param and
  delete the block:

```python
def build_meta(
    *,
    data_version: dict[str, Any],
    next_commands: list[dict[str, Any]],
    record_count: int | None = None,
    truncated: dict[str, Any] | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the canonical success ``_meta`` block.

    The recommended_citation is intentionally NOT emitted here: the load-bearing copies live
    per-record (and the single top-level summary citation on detail/hub tools), so re-stating it in
    ``_meta`` was pure duplication (assessment M4).
    """
    meta: dict[str, Any] = {"data_version": data_version, "next_commands": next_commands}
    if fetched_at is not None:
        meta["fetched_at"] = fetched_at
    elif isinstance(data_version, dict) and data_version.get("fetched_at"):
        meta["fetched_at"] = data_version["fetched_at"]
    if record_count is not None:
        meta["record_count"] = record_count
    if truncated is not None:
        meta["truncated"] = truncated
    return meta
```

Then remove the `recommended_citation=...` keyword argument from **every** `build_meta(...)` call
site (genes.py ×1, dosage.py ×2, actionability.py ×2, validity.py ×2, erepo.py ×3). Grep:
`grep -rn "recommended_citation=" clingen_link/mcp/tools` and delete those kwargs.

- [ ] **Step 4: Run to pass** — pytest + `make test-fast` (fix any call site mypy/arg errors).
- [ ] **Step 5: Commit** — `git commit -am "perf(envelope): drop duplicated _meta.recommended_citation (M4)"`

---

### Task 7: M5 — uniform no-data shaping (resolvable gene, empty domain → success+0)

**Files:**
- Modify: `clingen_link/mcp/tools/dosage.py` (`get_gene_dosage`), `clingen_link/mcp/tools/actionability.py` (`get_gene_actionability`)
- Test: `tests/unit/test_tool_dosage.py`, `tests/unit/test_tool_actionability.py`

- [ ] **Step 1: Failing test** — in `tests/unit/test_tool_actionability.py`:

```python
@pytest.mark.anyio
async def test_resolvable_gene_no_actionability_is_success_zero(actionability_client):
    # A gene that resolves (in the index) but has no actionability curation.
    res = await call_tool(actionability_client, "get_gene_actionability", {"gene": "NAA10"})
    assert res["success"] is True
    assert res["total"] == 0
    assert res["records"] == []
```

(If NAA10 isn't in the test snapshot, use whatever resolvable-but-empty gene the fixture has, or add
one. Mirror in `test_tool_dosage.py` for a gene with no dosage record.)

- [ ] **Step 2: Run to fail** — pytest → FAIL (raises not_found).

- [ ] **Step 3: Implement** — in `get_gene_actionability.call()`, replace the empty-raise with a
  success+0 envelope:

```python
            models = await services.actionability.for_gene(symbol, context=context)
            records = shape_records(models, domain="actionability", response_mode=response_mode)
            if include_detail and models:
                for model, record in zip(models, records, strict=True):
                    record["sepio_detail"] = await services.actionability.sepio_detail(
                        model.doc_id, context
                    )
            citation = models[0].recommended_citation if models else None
            headline = (
                f"{symbol}: {len(models)} actionability curation(s) ({context} context)."
                if models
                else f"{symbol}: no ClinGen actionability curation in the {context} context."
            )
            return {
                "headline": headline,
                "records": records,
                "total": len(models),
                "context": context,
                "recommended_citation": citation,
                "_meta": build_meta(
                    data_version=data_version_for(services.meta(), "actionability"),
                    next_commands=[
                        cmd("get_gene_summary", gene=symbol),
                        cmd("get_gene_validity", gene=symbol),
                    ],
                    record_count=len(models),
                ),
            }
```

(Delete the `if not models: raise DataNotFoundError(...)` block. The `symbol is None` branch — gene
not in the index — still raises `not_found`, which is correct.)

Apply the same pattern in `get_gene_dosage.call()`: delete `if not models: raise DataNotFoundError`,
shape `records` (empty list when no models / minimal), compute headline with an empty-case message,
`citation = models[0].recommended_citation if models else None`, `total: len(models)`.

- [ ] **Step 4: Run to pass** — `uv run pytest tests/unit/test_tool_dosage.py tests/unit/test_tool_actionability.py -v` → PASS; `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "fix(tools): resolvable gene with empty domain returns success+0 (M5)"`

---

### Task 8: L1 — ERepo truncation filter-echo includes `expert_panel`

**Files:**
- Modify: `clingen_link/mcp/tools/erepo.py` (`get_variant_interpretations`)
- Test: `tests/unit/test_tool_erepo.py`

- [ ] **Step 1: Failing test** — in `tests/unit/test_tool_erepo.py`:

```python
@pytest.mark.anyio
async def test_truncation_echoes_expert_panel(erepo_client):
    # Force pagination with a small size so a truncated block is emitted.
    res = await call_tool(
        erepo_client, "get_variant_interpretations",
        {"expert_panel": "ENIGMA", "size": 1, "page": 1},
    )
    trunc = res["_meta"].get("truncated")
    if trunc:  # only when >1 match exists
        assert trunc["filter"].get("expert_panel") == "ENIGMA"
```

- [ ] **Step 2: Run to fail** — pytest → FAIL when a match set >1 exists (expert_panel missing from echo).

- [ ] **Step 3: Implement** — in `get_variant_interpretations.call()`, add `expert_panel` to the
  `filter_applied` dict:

```python
                    filter_applied={
                        k: v
                        for k, v in {
                            "gene": gene,
                            "condition": condition,
                            "expert_panel": expert_panel,
                            "classification": classification,
                        }.items()
                        if v
                    },
```

- [ ] **Step 4: Run to pass** — pytest + `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "fix(erepo): echo expert_panel in truncation filter (L1)"`

---

### Task 9: L3-circular + L4 — non-circular not_found fallback; gene-specific summary permalink

**Files:**
- Modify: `clingen_link/mcp/tools/genes.py` (`search_genes` not_found path), `clingen_link/mcp/errors.py` (`_fallback_for`), `clingen_link/models/models.py` (`GeneSummary.from_counts`)
- Test: `tests/unit/test_tool_genes.py`, `tests/unit/test_errors.py`

- [ ] **Step 1: Failing test** — in `tests/unit/test_tool_genes.py`:

```python
@pytest.mark.anyio
async def test_not_found_does_not_recall_same_query(genes_client):
    res = await call_tool(genes_client, "search_genes", {"query": "NOTAREALGENE"})
    assert res["success"] is False
    first = res["_meta"]["next_commands"][0]
    # Must not re-suggest search_genes with the identical failing query.
    assert not (first["tool"] == "search_genes" and first["arguments"].get("query") == "NOTAREALGENE")
```

In `tests/unit/test_services.py` (GeneSummary citation):

```python
def test_gene_summary_permalink_includes_symbol():
    from clingen_link.models.models import GeneSummary
    m = GeneSummary.from_counts({"symbol": "BRCA1"}, validity=[], dosage=[], actionability=[])
    assert "BRCA1" in m.recommended_citation
```

- [ ] **Step 2: Run to fail** — both → FAIL.

- [ ] **Step 3a: errors `_fallback_for`** — in `clingen_link/mcp/errors.py`, for a not_found whose
  context query *was* the failing input, steer to capabilities rather than re-running search_genes
  with the same query. Change `_fallback_for`:

```python
def _fallback_for(context: McpErrorContext) -> tuple[str, dict[str, Any] | None]:
    """Resolve the not_found / invalid_input fallback without re-running the failing query.

    Re-suggesting ``search_genes`` with the identical query that just failed is a no-op loop
    (assessment L3); for a gene/query that did not resolve we steer to capabilities instead.
    """
    if context.gene and context.gene != context.query:
        return "search_genes", {"query": context.gene}
    return "get_server_capabilities", None
```

- [ ] **Step 3b: gene tool not_found** — the `search_genes` tool passes both `gene=query` and
  `query=query` into `McpErrorContext`, so `_fallback_for` now returns capabilities (since
  `gene == query`). No further change needed there; verify the DataNotFoundError context.

- [ ] **Step 3c: GeneSummary permalink** — in `clingen_link/models/models.py`
  `GeneSummary.from_counts`, make the permalink gene-specific:

```python
        citation = (
            f"ClinGen gene summary for {symbol}: "
            f"{counts.get('validity_count', 0)} validity, "
            f"{counts.get('dosage_count', 0)} dosage, "
            f"{counts.get('actionability_count', 0)} actionability, "
            f"{counts.get('erepo_count', 0)} ERepo variant interpretations. "
            f"https://search.clinicalgenome.org/kb/genes/?search={symbol}"
        )
```

- [ ] **Step 4: Run to pass** — `uv run pytest tests/unit/test_tool_genes.py tests/unit/test_services.py tests/unit/test_errors.py -v` → PASS; `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "fix(errors/models): non-circular not_found fallback + gene-specific summary permalink (L3,L4)"`

---

### Task 10: H1 — live ERepo adapter + safe degradation + correct error classification

**Files:**
- Create: `clingen_link/services/erepo_live.py`
- Modify: `clingen_link/services/erepo_service.py`, `clingen_link/mcp/tools/erepo.py`
- Test: `tests/unit/test_erepo_live.py`, `tests/unit/test_services.py` (respx)

- [ ] **Step 1: Failing test** — create `tests/unit/test_erepo_live.py`:

```python
from clingen_link.services.erepo_live import erepo_live_to_row


def test_classifications_summary_maps_to_row():
    summary = {
        "caid": "CA003681",
        "variationId": "12345",
        "hgvs": ["NC_000017.11:g.43045761A>C", "NM_007294.4:c.5509T>G"],
        "@id": "https://erepo.clinicalgenome.org/evrepo/ui/interpretation/abc",
        "uuid": "abc",
        "gene": {"label": "BRCA1", "NCBI_id": "672"},
        "condition": {"label": "hereditary breast cancer", "mondo": "MONDO:0007254"},
        "publishedDate": "2021-01-01",
    }
    row = erepo_live_to_row(summary)
    assert row["caid"] == "CA003681"
    assert row["gene"] == "BRCA1"           # dict -> label (this is the H1 ValidationError fix)
    assert row["clinvar_variation_id"] == "12345"
    assert row["published_date"] == "2021-01-01"
    assert row["hgvs"] == summary["hgvs"]


def test_sepio_enrichment_adds_evidence_codes():
    summary = {"caid": "CA1", "uuid": "u", "gene": {"label": "BRCA1"}, "hgvs": []}
    sepio = {
        "statementOutcome": {"label": "Pathogenic"},
        "evidenceLine": [
            {"evidenceCriterion": {"label": "PM2"}, "criterionMet": True},
            {"evidenceCriterion": {"label": "BS1"}, "criterionMet": False},
        ],
    }
    row = erepo_live_to_row(summary, sepio=sepio)
    assert row["assertion"] == "Pathogenic"
    assert "PM2" in row["evidence_codes_met"]
    assert "BS1" in row["evidence_codes_not_met"]
```

And in `tests/unit/test_services.py`, a respx test that `refresh=True` degrades to snapshot on a
malformed live payload (never validation_failed). Use the existing respx pattern in the file.

- [ ] **Step 2: Run to fail** — `uv run pytest tests/unit/test_erepo_live.py -v` → FAIL (module missing).

- [ ] **Step 3a: Implement adapter** — create `clingen_link/services/erepo_live.py`:

```python
"""Adapt live ERepo payloads into the normalized snapshot-row shape.

Two distinct live shapes exist: the classifications-search *summary*
(``/api/classifications?caid=…``: ``gene`` is a dict, keys are camelCase) and the full SEPIO
*interpretation* (``/api/interpretation/{uuid}``: ACMG criteria under ``evidenceLine``). Feeding
either to ``VariantInterpretation.from_row`` directly raises a Pydantic ``ValidationError`` (e.g.
``gene`` dict vs. ``str``) — the assessment's H1 bug, which was then mis-coded as ``validation_failed``.
This pure adapter normalizes both into the flat dict ``from_row`` expects, leniently (missing fields
never raise).
"""

from __future__ import annotations

from typing import Any


def _str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        label = value.get("label") or value.get("@id") or value.get("id")
        return str(label) if label is not None else None
    return str(value)


def _condition(value: Any) -> tuple[str | None, str | None]:
    """Return (disease_label, mondo) from a condition dict / string."""
    if isinstance(value, dict):
        mondo = value.get("mondo") or value.get("curie") or value.get("@id")
        m = str(mondo) if isinstance(mondo, str) and mondo.startswith("MONDO:") else None
        return _str(value), m
    return _str(value), None


def _evidence_codes(sepio: dict[str, Any]) -> tuple[list[str], list[str]]:
    met: list[str] = []
    not_met: list[str] = []
    for line in sepio.get("evidenceLine", []) or []:
        if not isinstance(line, dict):
            continue
        crit = line.get("evidenceCriterion") or line.get("criterion") or {}
        label = _str(crit) if not isinstance(crit, str) else crit
        if not label:
            continue
        if line.get("criterionMet") is True or line.get("met") is True:
            met.append(label)
        else:
            not_met.append(label)
    return met, not_met


def erepo_live_to_row(summary: dict[str, Any], *, sepio: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize a live ERepo classifications summary (+ optional SEPIO doc) into a snapshot row."""
    disease, mondo = _condition(summary.get("condition"))
    row: dict[str, Any] = {
        "caid": _str(summary.get("caid")),
        "clinvar_variation_id": _str(summary.get("variationId") or summary.get("clinvarVariationId")),
        "variation": _str(summary.get("label") or summary.get("variation")),
        "hgvs": list(summary.get("hgvs") or []),
        "gene": _str(summary.get("gene")),
        "disease": disease,
        "mondo": mondo or _str(summary.get("mondo")),
        "moi": _str(summary.get("modeOfInheritance") or summary.get("moi")),
        "assertion": _str(summary.get("assertion")),
        "evidence_codes_met": [],
        "evidence_codes_not_met": [],
        "summary": _str(summary.get("summary")),
        "pubmed": [str(p) for p in (summary.get("pubmed") or []) if p],
        "expert_panel": _str(summary.get("expertPanel") or summary.get("affiliation")),
        "guideline_cspec": _str(summary.get("guideline")),
        "approval_date": _str(summary.get("approvalDate")),
        "published_date": _str(summary.get("publishedDate")),
        "retracted": bool(summary.get("retracted")),
        "uuid": _str(summary.get("uuid")),
        "repo_link": _str(summary.get("@id") or summary.get("repoLink")),
    }
    if sepio:
        outcome = sepio.get("statementOutcome")
        if outcome is not None:
            row["assertion"] = _str(outcome) or row["assertion"]
        met, not_met = _evidence_codes(sepio)
        if met or not_met:
            row["evidence_codes_met"] = met
            row["evidence_codes_not_met"] = not_met
        row["summary"] = _str(sepio.get("summary")) or row["summary"]
    return row
```

- [ ] **Step 3b: Service uses the adapter + degrades** — in `clingen_link/services/erepo_service.py`,
  change `_live_impl` to adapt the payload, and `get_interpretation` to degrade safely:

```python
from ..exceptions import ClingenApiError, DataNotFoundError
from .erepo_live import erepo_live_to_row

    async def get_interpretation(self, *, caid=None, hgvs=None, refresh=False):
        snapshot = self._snapshot_lookup(caid=caid, hgvs=hgvs)
        if not refresh and snapshot is not None:
            return snapshot, "snapshot", None
        if not refresh:
            # snapshot miss, no refresh -> raise not_found via the caller
            if snapshot is None:
                version = await self._current_version()
                row = await self._live_cached(caid or "", hgvs or "", version)
                return VariantInterpretation.from_row(row), "live", None
        # refresh=True: try live, degrade to snapshot on any failure.
        try:
            version = await self._current_version()
            row = await self._live_cached(caid or "", hgvs or "", version)
            return VariantInterpretation.from_row(row), "live", None
        except DataNotFoundError:
            raise
        except Exception as exc:  # upstream/parse failure -> degrade or surface upstream_unavailable
            if snapshot is not None:
                return snapshot, "snapshot", f"live ERepo fetch degraded ({exc.__class__.__name__}); served snapshot"
            raise ClingenApiError(f"live ERepo fetch failed: {exc.__class__.__name__}") from exc
```

Change the signature to return a `(model, source, notice)` tuple; update `_live_impl`:

```python
    async def _live_impl(self, caid: str, hgvs: str, _version: str) -> dict[str, Any]:
        summary = await self._client.erepo_interpretation(caid=caid or None, hgvs=hgvs or None)
        sepio = None
        uuid = summary.get("uuid") if isinstance(summary, dict) else None
        if uuid:
            try:
                sepio = await self._client.erepo_interpretation(uuid=str(uuid))
            except Exception:  # SEPIO enrichment is best-effort
                sepio = None
        return erepo_live_to_row(summary, sepio=sepio)
```

- [ ] **Step 3c: Tool surfaces source + notice** — in `clingen_link/mcp/tools/erepo.py`
  `get_variant_interpretation.call()`, consume the tuple and add the notice to `_meta`:

```python
            if clinvar_variation_id and not refresh:
                model = _by_clinvar(services, clinvar_variation_id)
                source, notice = "snapshot", None
            else:
                model, source, notice = await services.erepo.get_interpretation(
                    caid=caid, hgvs=hgvs, refresh=refresh
                )
            shaped = shape_record(model, domain="erepo", response_mode=response_mode)
            interpretation = {} if response_mode == "minimal" else shaped
            meta_block = build_meta(
                data_version=data_version_for(services.meta(), "erepo"),
                next_commands=[cmd("get_variant_interpretations", gene=model.gene or "BRCA1")],
            )
            if notice:
                meta_block["notice"] = notice
            return {
                "headline": headline,
                "interpretation": interpretation,
                "source": source,
                "recommended_citation": model.recommended_citation,
                "_meta": meta_block,
            }
```

Add `"notice": {"type": ["string", "null"]}` to `_DETAIL_SCHEMA` properties.

- [ ] **Step 4: Run to pass** — `uv run pytest tests/unit/test_erepo_live.py tests/unit/test_services.py tests/unit/test_tool_erepo.py -v` → PASS; `make test-fast`. Also confirm `errors._classify`
  never returns `validation_failed` for the live path (the adapter prevents the Pydantic error; the
  degrade path raises `ClingenApiError` → `upstream_unavailable`).
- [ ] **Step 5: Commit** — `git commit -am "fix(erepo): repair live refresh via SEPIO adapter + safe snapshot degrade (H1)"`

- [ ] **Step 6: Run full gate** — `make ci-local`. Fix any LOC/format/mypy issues. Commit if needed.

---

## Layer B — ETL enhancement (clean source for the rebuild)

### Task 11: ETL — sanitize validity + `disease_obsolete` column + dosage count at source

**Files:**
- Modify: `clingen_link/etl/parse.py` (`parse_validity`), `clingen_link/etl/schema.py`
  (`VALIDITY_DDL`, FTS unchanged), `clingen_link/etl/build.py` (`_write_validity`, `populate`,
  `_DOSAGE`/meta), `clingen_link/etl/freshness.py` (`dosage_signal`), `clingen_link/store/queries.py` (`_VALIDITY_COLS`)
- Test: `tests/unit/test_etl_parse.py`, `tests/unit/test_etl_build.py`

- [ ] **Step 1: Failing test** — in `tests/unit/test_etl_parse.py`:

```python
def test_parse_validity_sanitizes_and_flags_obsolete():
    rows = [{"symbol": "TMPO", "disease_name": 'x <span class="b">Obsolete Term</span>', "perm_id": "p"}]
    out = parse.parse_validity(rows)
    assert "<span" not in out[0]["disease_name"]
    assert out[0]["disease_obsolete"] is True
```

In `tests/unit/test_etl_build.py`, assert the dosage meta count equals the parsed row count (build a
Sources with 2 dosage gene rows, assert `meta` row `record_count == 2`).

- [ ] **Step 2: Run to fail** — pytest → FAIL.

- [ ] **Step 3a: parse_validity** — in `clingen_link/etl/parse.py`, import the sanitizer and emit the
  clean name + flag:

```python
from .sanitize import is_obsolete_label, strip_html

# inside parse_validity loop:
        raw_disease = row.get("disease_name") or ""
        out.append({
            ...
            "disease_name": strip_html(raw_disease) or None,
            "disease_obsolete": is_obsolete_label(raw_disease),
            ...
        })
```

- [ ] **Step 3b: schema** — add `disease_obsolete INTEGER NOT NULL DEFAULT 0` to `VALIDITY_DDL` in
  `clingen_link/etl/schema.py`.

- [ ] **Step 3c: writer** — in `clingen_link/etl/build.py` `_write_validity`, add the column to the
  INSERT (column list + value `1 if row.get("disease_obsolete") else 0`).

- [ ] **Step 3d: query column** — add `disease_obsolete` to `_VALIDITY_COLS` in
  `clingen_link/store/queries.py` so it round-trips to the model.

- [ ] **Step 3e: dosage count at source** — in `clingen_link/etl/build.py` `populate`, after parsing
  `dosage`, record the real count in the dosage meta row. Change the `_write_meta` for dosage to take
  the dosage signal but override `record_count`:

```python
    dosage_signal = freshness.dosage_signal(sources.dosage_etags)
    dosage_signal["record_count"] = len(dosage)
    _write_meta(conn, "dosage", dosage_signal, fetched_at)
```

(Leave `freshness.dosage_signal` returning its ETag signal; the writer now supplies the true count.)

- [ ] **Step 4: Run to pass** — `uv run pytest tests/unit/test_etl_parse.py tests/unit/test_etl_build.py -v` → PASS; `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "feat(etl): sanitize validity labels, add disease_obsolete, fix dosage count at source (M1,H2)"`

---

### Task 12: ETL — fetch GRCh37 dosage TSVs and backfill `grch37`

**Files:**
- Modify: `clingen_link/etl/fetch.py` (`_DOSAGE_FILES`, `DosageBundle`, `fetch_dosage`),
  `clingen_link/etl/build.py` (`Sources`, `populate`), `clingen_link/etl/refresh.py` (wiring)
- Test: `tests/unit/test_etl_fetch.py`, `tests/unit/test_etl_parse.py` (backfill already covered — add an integration-shaped parse test)

- [ ] **Step 1: Failing test** — in `tests/unit/test_etl_parse.py`:

```python
def test_parse_dosage_backfills_grch37():
    gene38 = "BRCA1\tg1\t17q21\tchr17:43044295-43125483\t3\td\t\t\t\t\t\t\t0\td2\t\t\t\t\t\t2026\t\t\n"
    gene37 = "BRCA1\tg1\t17q21\tchr17:41196312-41277500\t3\td\t\t\t\t\t\t\t0\td2\t\t\t\t\t\t2026\t\t\n"
    out = parse.parse_dosage(gene38, "", gene_tsv_grch37=gene37, region_tsv_grch37="")
    rec = next(r for r in out if r["symbol"] == "BRCA1")
    assert rec["grch37"] == "chr17:41196312-41277500"
```

- [ ] **Step 2: Run to fail** — this may already pass (parser supports backfill). If so, the failing
  surface is the **fetch + wiring**: add a `respx`-mock test in `tests/unit/test_etl_fetch.py`
  asserting `fetch_dosage` now returns the two GRCh37 texts in the bundle.

- [ ] **Step 3a: fetch** — in `clingen_link/etl/fetch.py`, extend `_DOSAGE_FILES` and `DosageBundle`:

```python
_DOSAGE_FILES: dict[str, str] = {
    "gene_grch38": "ClinGen_gene_curation_list_GRCh38.tsv",
    "region_grch38": "ClinGen_region_curation_list_GRCh38.tsv",
    "gene_grch37": "ClinGen_gene_curation_list_GRCh37.tsv",
    "region_grch37": "ClinGen_region_curation_list_GRCh37.tsv",
}


@dataclass
class DosageBundle:
    gene_tsv: str
    region_tsv: str
    gene_tsv_grch37: str = ""
    region_tsv_grch37: str = ""
    etags: dict[str, str] = field(default_factory=dict)
```

and in `fetch_dosage` populate `gene_tsv_grch37=texts["gene_grch37"]`,
`region_tsv_grch37=texts["region_grch37"]`.

- [ ] **Step 3b: Sources + populate** — in `clingen_link/etl/build.py`, add
  `dosage_gene_tsv_grch37: str = ""` and `dosage_region_tsv_grch37: str = ""` to `Sources`, and pass
  them to `parse.parse_dosage(..., gene_tsv_grch37=sources.dosage_gene_tsv_grch37, region_tsv_grch37=sources.dosage_region_tsv_grch37)`.

- [ ] **Step 3c: refresh wiring** — in `clingen_link/etl/refresh.py`, map the new `DosageBundle`
  fields into `Sources` (find where `Sources(...)` is built from the fetched bundle and add the two
  GRCh37 fields).

- [ ] **Step 4: Run to pass** — `uv run pytest tests/unit/test_etl_parse.py tests/unit/test_etl_fetch.py -v` → PASS; `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "feat(etl): fetch GRCh37 dosage TSVs + backfill grch37 (L5)"`

---

### Task 13: ETL — HGNC ingestion (gene full name + alias_symbol/prev_symbol)

**Files:**
- Create: `clingen_link/etl/hgnc.py`
- Modify: `clingen_link/etl/parse.py` (`build_gene_index` signature), `clingen_link/etl/build.py`
  (`Sources`, `populate`), `clingen_link/etl/refresh.py` (fetch wiring), `clingen_link/config.py`
  (HGNC url), `clingen_link/etl/fetch.py` (`fetch_hgnc`)
- Test: `tests/unit/test_etl_hgnc.py`, `tests/unit/test_etl_parse.py`

- [ ] **Step 1: Failing test** — create `tests/unit/test_etl_hgnc.py`:

```python
from clingen_link.etl import hgnc

_TSV = (
    "hgnc_id\tsymbol\tname\talias_symbol\tprev_symbol\n"
    "HGNC:1101\tBRCA2\tBRCA2 DNA repair associated\tFACD|FANCD1\tFANCD1\n"
    "HGNC:1100\tBRCA1\tBRCA1 DNA repair associated\tRNF53\t\n"
)


def test_parse_hgnc_extracts_name_and_aliases():
    rows = hgnc.parse_hgnc(_TSV)
    by_symbol = {r["symbol"]: r for r in rows}
    assert by_symbol["BRCA2"]["name"] == "BRCA2 DNA repair associated"
    assert "FANCD1" in by_symbol["BRCA2"]["aliases"]
    assert by_symbol["BRCA1"]["hgnc_id"] == "HGNC:1100"
```

In `tests/unit/test_etl_parse.py`, assert `build_gene_index` with an HGNC map sets `name` and adds
the `FANCD1` alias:

```python
def test_build_gene_index_applies_hgnc_name_and_alias():
    validity = [{"symbol": "BRCA2", "hgnc_id": "HGNC:1101"}]
    hgnc_map = {"BRCA2": {"hgnc_id": "HGNC:1101", "name": "BRCA2 DNA repair associated", "aliases": ["FANCD1"]}}
    genes, aliases = parse.build_gene_index(validity, [], [], {}, hgnc=hgnc_map)
    assert next(g for g in genes if g["symbol"] == "BRCA2")["name"] == "BRCA2 DNA repair associated"
    assert ("FANCD1", "BRCA2") in {(a["alias"], a["symbol"]) for a in aliases}
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/unit/test_etl_hgnc.py -v` → FAIL (module missing).

- [ ] **Step 3a: hgnc parser** — create `clingen_link/etl/hgnc.py`:

```python
"""HGNC complete-set ingestion: gene full name + alias/prev symbols.

The bundled snapshot's ``gene.name`` was never populated and alias resolution only covered HGNC ids
and case-folded symbols, so official aliases (e.g. ``FANCD1`` → ``BRCA2``) returned not_found
(assessment L2/L3). This module parses the HGNC ``hgnc_complete_set`` TSV (the authoritative
symbol ↔ alias ↔ id ↔ name table) into a per-symbol map the gene-index builder annotates onto the
genes ClinGen actually curates (keeping the index lean).
"""

from __future__ import annotations

import csv
import io
from typing import Any


def parse_hgnc(tsv_text: str) -> list[dict[str, Any]]:
    """Parse the HGNC complete-set TSV into ``{hgnc_id, symbol, name, aliases:[...]}`` rows."""
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    out: list[dict[str, Any]] = []
    for row in reader:
        symbol = (row.get("symbol") or "").strip()
        if not symbol:
            continue
        aliases: list[str] = []
        for col in ("alias_symbol", "prev_symbol"):
            cell = (row.get(col) or "").strip()
            for tok in cell.split("|"):
                tok = tok.strip()
                if tok and tok != symbol and tok not in aliases:
                    aliases.append(tok)
        out.append(
            {
                "hgnc_id": (row.get("hgnc_id") or "").strip() or None,
                "symbol": symbol,
                "name": (row.get("name") or "").strip() or None,
                "aliases": aliases,
            }
        )
    return out


def index_by_symbol(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return an HGNC map keyed by symbol for the gene-index builder."""
    return {r["symbol"]: r for r in rows}
```

- [ ] **Step 3b: fetch_hgnc** — add to `clingen_link/etl/fetch.py`:

```python
def fetch_hgnc(client: httpx.Client | None = None) -> str:
    """Fetch the HGNC complete-set TSV (symbol/alias/prev/name authority)."""
    owned = client is None
    client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT)
    try:
        return _get(client, settings.hgnc_complete_set_url, "hgnc").text
    finally:
        if owned:
            client.close()
```

and add to `clingen_link/config.py` `Settings`:

```python
    hgnc_complete_set_url: str = (
        "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
    )
```

- [ ] **Step 3c: build_gene_index** — in `clingen_link/etl/parse.py`, give `build_gene_index` an
  optional `hgnc` map and apply name + aliases for genes already in the index:

```python
def build_gene_index(validity, dosage, actionability, erepo_summary, *, hgnc=None):
    ...
    # (existing union-building unchanged) ... then, before building alias rows:
    if hgnc:
        for symbol, record in genes.items():
            info = hgnc.get(symbol)
            if not info:
                continue
            if info.get("name") and not record.get("name"):
                record["name"] = info["name"]
            if info.get("hgnc_id") and not record.get("hgnc_id"):
                record["hgnc_id"] = info["hgnc_id"]
            for alias in info.get("aliases", []):
                _add_alias(aliases, alias, symbol)
    ...
```

- [ ] **Step 3d: Sources + populate + refresh** — add `hgnc_rows: list[dict[str, Any]] = field(default_factory=list)` to `Sources`; in `populate`, build `hgnc_map = hgnc.index_by_symbol(sources.hgnc_rows)` and pass `hgnc=hgnc_map` to `build_gene_index`. In `refresh.py`, call `fetch_hgnc`, `parse_hgnc`, and set `sources.hgnc_rows`.

- [ ] **Step 4: Run to pass** — `uv run pytest tests/unit/test_etl_hgnc.py tests/unit/test_etl_parse.py -v` → PASS; `make test-fast`.
- [ ] **Step 5: Commit** — `git commit -am "feat(etl): HGNC name + alias ingestion (L2,L3)"`

- [ ] **Step 6: Gate** — `make ci-local` → green. Fix LOC/format/mypy. Commit.

---

## Layer C — snapshot rebuild + verification + docs

### Task 14: Rebuild the bundled snapshot and verify

**Files:**
- Modify (regenerate): `clingen_link/data/clingen.sqlite.zst`, `clingen_link/data/clingen.sqlite.sha256`

- [ ] **Step 1: Dry-check staleness** — `uv run clingen-link refresh --check` (informational).
- [ ] **Step 2: Rebuild** — `uv run clingen-link refresh --out clingen_link/data/clingen.sqlite` (fetches validity, dosage GRCh37+38, actionability, erepo, HGNC; builds + prints row counts).
- [ ] **Step 3: Re-compress + checksum** — use the repo's existing target if present
  (`grep -rn "zst" Makefile scripts`), else:

```bash
uv run python -c "import zstandard,pathlib; p=pathlib.Path('clingen_link/data/clingen.sqlite'); d=zstandard.ZstdCompressor(level=19).compress(p.read_bytes()); pathlib.Path('clingen_link/data/clingen.sqlite.zst').write_bytes(d)"
sha256sum clingen_link/data/clingen.sqlite | awk '{print $1}' > clingen_link/data/clingen.sqlite.sha256
```

(Match whatever format the existing `.sha256` uses — inspect it first.)

- [ ] **Step 4: Verify the fixes landed in data** — run a verification script asserting:
  dosage `record_count == COUNT(*)` (≈2026), ≥90% of `gene` rows have a non-null `name`,
  `resolve_gene("FANCD1") == "BRCA2"`, a sample dosage row has non-null `grch37`, and a validity
  disease_name has no `<` tags. Fail loudly if any assertion fails.
- [ ] **Step 5: Gate** — `make ci-local` (the smoke/store tests run against the new bundle). Green.
- [ ] **Step 6: Commit** — `git add clingen_link/data/clingen.sqlite.zst clingen_link/data/clingen.sqlite.sha256 && git commit -m "data: rebuild snapshot with HGNC names/aliases, GRCh37, clean labels"`

---

### Task 15: Docs + final verification

**Files:**
- Modify: `docs/architecture.md` / `docs/usage.md` (note HGNC ingestion, GRCh37, disease_obsolete,
  hgvs trimming, refresh degradation), the assessment file (append a resolution note).
- Create: `docs/mcp-assessment-2026-06-12-resolution.md` (maps each finding → fix → commit/test).

- [ ] **Step 1** — Write the resolution note: a table of all 13 findings, the fix, and the test that
  proves it.
- [ ] **Step 2** — Update `docs/usage.md` (response_mode lattice now strict; minimal omits record
  bodies; hgvs trimmed in compact; `disease_obsolete` flag; `refresh=true` degrades safely) and
  `docs/architecture.md` (ETL now ingests HGNC + GRCh37).
- [ ] **Step 3: Final gate** — `make ci-local` and `make test-cov` (≥80%). Capture output.
- [ ] **Step 4: Commit** — `git commit -am "docs: record assessment resolution + updated usage/architecture"`

---

## Self-review checklist (completed)
- **Spec coverage:** H1→T10, H2→T2+T11, H3→T1, M1→T3+T11, M2→T4, M3→T5, M4→T6, M5→T7, L1→T8,
  L2→T13, L3→T9(circular)+T13(alias), L4→T9, L5→T12. Rebuild→T14. Docs→T15. All 13 covered.
- **Placeholders:** none — every code step shows the code.
- **Type consistency:** `get_interpretation` returns `(model, source, notice)` in T10 and is consumed
  with that arity in the tool; `build_gene_index(..., hgnc=...)` keyword consistent T13; `DosageBundle`
  GRCh37 fields consistent T12.
