# clingen-link 9.5/10 Hardening — Design Spec

**Date:** 2026-06-12
**Author:** MCP engineering (autonomous, superpowers workflow)
**Driver:** `docs/mcp-assessment-2026-06-12.md` (black-box LLM-consumer assessment, scored **8/10**)
**Goal:** Resolve every finding (H1–H3, M1–M5, L1–L5) to current MCP best practice and lift the
server to **>9.5/10**, end-to-end (code + tests + bundled-snapshot rebuild + docs).

---

## 1. Context & constraints

clingen-link is a FastMCP v3 server over a read-only bundled SQLite snapshot
(`clingen_link/data/clingen.sqlite.zst`) with a thin live HTTP layer for single-record
drill-down. The assessment is black-box: it re-runs the 13 tools against the **served snapshot**,
so to move the score we must change both **code** and, where a finding is data-resident, the
**bundled snapshot** itself.

Hard constraints (AGENTS.md):
- 600-LOC/module cap (`make lint-loc`), Ruff (line length 100), mypy **strict**, ≥80% coverage.
- `uv` only; `make ci-local` is the gate.
- Do **not** hand-edit the snapshot bundle or `tests/fixtures/`. Snapshot regeneration is the
  sanctioned `clingen-link refresh` path; new ETL tests use **inline in-memory inputs**, not new
  fixture files.

Verified facts this session:
- Bundled snapshot `meta` has `dosage.record_count = 2`; actual `dosage` rows = **2026** (1513 gene +
  513 region). `gene.name` populated on **0** rows. `FANCD1` alias absent.
- Live network reachable from the ETL host (HGNC complete set, GRCh37 dosage TSVs, ERepo
  classifications all return 2xx) → a real refresh is feasible.
- MCP current protocol revision is **2025-11-25** (matches the server's advertised version).
- H1 root cause confirmed: the live `/api/classifications?caid=…&format=json` payload returns
  `gene` as a **dict** `{label, NCBI_id}` and camelCase keys (`publishedDate`, `variationId`);
  feeding it to `VariantInterpretation.from_row` (which expects `gene: str`) raises a Pydantic
  `ValidationError`, which `errors._classify` buckets as `validation_failed` →
  `reformulate_input`. So both the parse and the classification are wrong.

### Best-practice deltas adopted (from MCP spec 2025-11-25 + Anthropic tool-design guidance)
1. Error taxonomy is about **recoverability**, not blame: an upstream/parse fault must surface as a
   retryable upstream code, never as `invalid_input`/`validation_failed`. (Drives H1.)
2. Token economy: response-mode tiers must be a strict subset lattice; trim large arrays; avoid
   envelope duplication; progressive disclosure for big blobs. (Drives M2, M3, M4.)
3. `_meta` custom keys only need to avoid the reserved `mcp`/`modelcontextprotocol` prefixes — the
   existing keys (`next_commands`, `data_version`, …) comply, so **reverse-DNS renaming is an
   explicit non-goal** (it would churn the praised, sibling-shared envelope for no scored gain).

---

## 2. Approach

Three layers, applied in order, each independently testable:

**A. Serve-time / pure-code fixes** — correct on the *current* bundle, no rebuild required:
H1, H2 (serve-time count), H3, M1 (serve-time sanitize), M2, M3, M4, M5, L1, L3-circular, L4.

**B. ETL enhancement** — make the *source* clean so a rebuild realizes the data-resident findings,
fully unit-tested with inline inputs: HGNC name+alias ingestion (L2, L3-alias), GRCh37 dosage
backfill (L5), ETL-side HTML sanitize + `disease_obsolete` (M1 at source), dosage `record_count`
from row count (H2 at source).

**B is defense-in-depth with A**: H2 and M1 are fixed *both* at serve-time (works now) *and* at the
ETL (clean for every future build). L2/L3-alias/L5 are realizable **only** via rebuild.

**C. Snapshot rebuild + re-bundle** — run `clingen-link refresh` with the new ETL, re-compress to
`.zst`, regenerate `.sha256`, verify row counts and the previously-failing cases, confirm
`make ci-local` stays green. This is what makes L2/L3/L5 visible to a black-box re-test.

Rationale for doing all three (vs. code-only): the assessment is black-box against the served
snapshot; L2 (`name`), L3 (`FANCD1`), and L5 (`grch37`) are physically absent from the bundle and
cannot be conjured at serve-time without putting HGNC fetches in the request path (forbidden by the
architecture). The rebuild is the sanctioned, already-automated (`data-refresh.yml`) mechanism.

---

## 3. Per-finding design

### H1 — `get_variant_interpretation refresh=true` (live ERepo) — **fix + safe-degrade**
- **New module** `clingen_link/services/erepo_live.py` (`erepo_live_to_row`): pure adapter mapping a
  live ERepo payload to the normalized snapshot-row dict `VariantInterpretation.from_row` consumes.
  Handles the **classifications-summary** shape (`gene{label}`→`gene`, `publishedDate`→
  `published_date`, `variationId`→`clinvar_variation_id`, `condition`→`disease`/`mondo`, `hgvs[]`,
  `uuid`, `caid`, `@id`→`repo_link`) and, when a `uuid` is available, **enriches** from the SEPIO
  `/api/interpretation/{uuid}` doc (`evidenceLine`→`evidence_codes_met`/`_not_met`,
  `statementOutcome`→`assertion`, `summary`). Lenient: missing SEPIO fields never raise.
- `ClingenClient.erepo_interpretation` returns the raw live dict (unchanged); the **service**
  (`ErepoService._live_impl`) runs it through `erepo_live_to_row` before `from_row`.
- **Graceful degradation** in `ErepoService.get_interpretation(refresh=True)`: try live → adapt →
  model. On *any* live-path exception, if a snapshot row exists, return it with `source="snapshot"`
  and a `_meta.notice` ("live ERepo fetch degraded; served snapshot"); else raise
  `ClingenApiError` → `upstream_unavailable` (retryable). A truly absent id → `DataNotFoundError`
  (`not_found`). **Never `validation_failed` for a well-formed CAID/HGVS.**
- The tool reports the real `source` ("live" | "snapshot") and surfaces the notice when degraded.
- **Test:** respx-mock the classifications + SEPIO endpoints with the real shapes from research;
  assert refresh=true returns enriched live data, and that a 5xx / malformed-payload degrades to
  snapshot (never `validation_failed`).

### H2 — Dosage `record_count: 2` → real count — **serve-time + ETL**
- **Serve-time (authoritative):** `Store.meta()` overrides each domain's `record_count` with a live
  `COUNT(*)` of the backing table (`validity`/`dosage`/`actionability`/`erepo`), cached per Store
  instance. Fixes capabilities, diagnostics, and every `_meta.data_version` immediately on the
  current bundle. Keeps the ETag string in `signal_value`.
- **ETL (source):** `build.populate` passes the actual parsed dosage row count into the dosage meta
  row instead of `len(etags)`; `freshness.dosage_signal` keeps the ETag `signal_value` but stops
  asserting a row count (the count is supplied by the writer).
- **Test:** in-memory snapshot with N dosage rows → `meta()["dosage"]["record_count"] == N`.

### H3 — HGNC-id resolution exact-match — **pure code**
- `queries.search_genes`: when `query` matches `^HGNC:\d+$`, do `WHERE g.hgnc_id = ?` equality (plus
  the alias-table exact hit) — never `LIKE` prefix. Non-HGNC text keeps the symbol/alias prefix
  search. `Store.resolve_gene` already does exact alias lookup (correct); this fixes the *candidate*
  list so a canonical HGNC id returns exactly its gene and a short id like `HGNC:11` no longer
  matches 25 rows.
- **Test:** `search_genes("HGNC:1100")` → only BRCA1; `HGNC:11` → 0/clean, not 25.

### M1 — Unsanitized HTML in `disease_name` + obsolescence flag — **serve-time + ETL**
- **New helper** `clingen_link/etl/sanitize.py` (`strip_html`, `is_obsolete_label`): remove tags,
  unescape entities, collapse whitespace; detect the `Obsolete Term` / `obsolete …` marker.
- **ETL:** `parse_validity` sanitizes `disease_name` and sets a structured `disease_obsolete: bool`.
  Schema gains a `disease_obsolete INTEGER` column; the validity writer + query select it.
- **Serve-time defense:** `ValidityAssertion.from_row` sanitizes `disease_name` and derives
  `disease_obsolete` even when the column is absent (older bundle), so the citation contract
  ("paste verbatim") never carries raw `<span…>`.
- **Test:** a row with embedded markup → clean `disease_name`, `disease_obsolete=True`, clean
  citation.

### M2 — Trim the ~51-element `hgvs[]` in minimal/compact — **pure code (biggest token win)**
- **New helper** `clingen_link/mcp/hgvs_select.py` (`canonical_hgvs`): from an HGVS list pick
  canonical genomic GRCh38 (`NC_…:g.`, prefer the highest/most-recent accession), the MANE/coding
  transcript (`NM_…:c.`), and the protein (`NP_…:p.` / `p.`) — at most 3, order-stable, deduped.
- `shaping.shape_record`: for `erepo` in **minimal/compact**, replace `hgvs` with `canonical_hgvs`
  and add `hgvs_count`; **standard/full** keep the full array. Implemented as a generic per-domain
  "array projector" hook so the rule is declared in one place.
- **Test:** compact erepo record `len(hgvs) <= 3` and includes the genomic+MANE+protein; full keeps
  all; `hgvs_count` reflects the original length.

### M3 — `response_mode` lattice consistency (minimal ⊆ compact ⊆ standard ⊆ full) — **pure code**
- `shape_record` gains an explicit **minimal** branch = compact projection (drop verbose + nulls +
  array-trim), guaranteeing minimal is a subset of compact (today minimal falls through to the
  *standard* branch → keeps nulls → inverted, the dosage bug).
- Per-record detail tools unify on the documented "minimal = headline + counts, no per-record
  lists": `get_gene_dosage` and `get_variant_interpretation` return `records: []` / omit the record
  body in `minimal` (headline already carries the answer), matching `get_gene_summary` and the list
  tools. The dosage headline is computed from an internally-shaped head record so minimal still
  answers haplo/triplo.
- **Test:** for every domain, assert `set(minimal) ⊆ set(compact) ⊆ set(standard) ⊆ set(full)` at
  the record level, and that no tier adds a null a tighter tier omitted.

### M4 — Envelope de-duplication — **pure code**
- Drop `recommended_citation` from `_meta` (`build_meta` stops emitting it); the load-bearing copies
  remain **per-record** (and the single top-level summary citation on detail/hub tools). This
  removes one full citation copy from every response — the assessment's "3–4×" → at most 2 (record +
  top-level), 1 for list tools (per-record only).
- `data_version` stays domain-scoped for every non-hub tool (already true); the gene hub remains
  legitimately cross-domain (it answers across all four), which is correct, not duplication.
- **Test:** assert `"recommended_citation" not in result["_meta"]` across all tools; per-record
  citations intact.

### M5 — Uniform "resolvable gene, no records in domain" → `success + total:0` — **pure code**
- `get_gene_dosage` and `get_gene_actionability`: when the gene **resolves** but the domain has no
  records, return `success` + `records: []` + `total: 0` + a headline ("no dosage/actionability
  records") instead of raising `DataNotFoundError`. `not_found` is reserved for a gene absent from
  the snapshot index entirely (unresolvable). Matches `get_gene_validity` / list tools.
- **Test:** a resolvable gene with empty domain → `success:true, total:0`; an unknown gene →
  `not_found`.

### L1 — ERepo truncation filter-echo includes `expert_panel` — **pure code**
- `get_variant_interpretations` adds `expert_panel` to `truncated.filter` so `to_restore` is
  reproducible for that filter. **Test:** truncate with `expert_panel="ENIGMA"` → echoed.

### L2 — Populate gene full `name` — **ETL + rebuild**
- HGNC ingestion (see B) sets `gene.name` from the HGNC `name` column; `search_genes`/
  `get_gene_summary` already surface it. **Test:** HGNC parse maps `symbol→name`.

### L3 — Alias gap + circular fallback — **ETL (alias) + pure code (fallback)**
- **Alias:** HGNC ingestion adds `alias_symbol` + `prev_symbol` (pipe-split) → `gene_alias`, so
  `FANCD1`→BRCA2 resolves. **Test:** parse adds the alias rows.
- **Circular fallback (code):** the `search_genes` not_found envelope must not re-suggest
  `search_genes` with the identical failing query. `errors._fallback_for` / the gene-tool not_found
  path point the first `next_command` at `get_server_capabilities` (or `get_gene_summary` only when
  a candidate exists), never a verbatim re-call of the failing query. **Test:** not_found
  `next_commands[0].arguments.query != failing_query`.

### L4 — `get_gene_summary` citation permalink is gene-specific — **pure code**
- `GeneSummary.from_counts` appends the symbol to the permalink
  (`…/kb/genes/?search={symbol}` per the CGGV gene page), not a bare `…/kb/genes/`. **Test:** symbol
  present in the citation URL.

### L5 — `grch37` populated (stop the doc overpromise) — **ETL + rebuild**
- ETL fetches the GRCh37 gene+region dosage TSVs and backfills `grch37` (parser already supports the
  backfill; only `fetch_dosage` + the refresh wiring need the GRCh37 files). The
  `get_gene_dosage`/dosage-citation docs that promise "GRCh37/GRCh38 coordinates" become true.
  **Test:** parse with both assemblies → `grch37` populated; join on gene id.

---

## 4. ETL enhancement detail (layer B)

- **New** `etl/hgnc.py`: `fetch_hgnc(client)` (bulk `hgnc_complete_set.txt`) + `parse_hgnc(tsv)` →
  `{hgnc_id, symbol, name, aliases:[...]}` (pipe-split `alias_symbol`+`prev_symbol`). `build_gene_index`
  accepts the HGNC map and fills `name` + extra `gene_alias` rows for genes present in the snapshot
  domains (HGNC is the authority; we only annotate genes ClinGen actually curates, keeping the index
  lean). Wired into `etl/refresh.py` and `etl/fetch.py` source set; `Sources` gains `hgnc_rows`.
- **GRCh37 dosage:** `_DOSAGE_FILES` gains the two GRCh37 files; `DosageBundle`/`Sources` carry
  them; `build.populate` passes them to `parse_dosage(..., gene_tsv_grch37=…, region_tsv_grch37=…)`.
- **dosage count:** `populate` records the real dosage row count in `meta`.
- **HTML sanitize:** `parse_validity` uses `etl/sanitize.py`.
- All new ETL code is unit-tested with **inline** TSV/JSON strings (no new fixture files), keeping
  parsers pure and deterministic. File-size: `parse.py` is at 460 LOC → HGNC parsing goes in the new
  `etl/hgnc.py`, not `parse.py`, to stay under the 600 cap.

## 5. Module / LOC plan
New files (all < 600 LOC): `services/erepo_live.py`, `mcp/hgvs_select.py`, `etl/sanitize.py`,
`etl/hgnc.py`. Edited files stay within budget (largest touched: `store/queries.py` 437,
`etl/parse.py` 460, `mcp/errors.py` 428 — all have headroom; HGNC logic deliberately lands in the
new `etl/hgnc.py`). `make lint-loc` enforced.

## 6. Testing strategy
- TDD per finding: a failing unit test first, then the fix. New tests extend the existing
  `tests/unit/test_*` modules (and add `test_erepo_live.py`, `test_hgvs_select.py`,
  `test_etl_hgnc.py`, `test_etl_sanitize.py`).
- respx mocks for the live ERepo classifications + SEPIO shapes (research-verified payloads).
- A lattice test asserting the minimal⊆compact⊆standard⊆full invariant across all domains.
- `make ci-local` (format-check, lint-ci, lint-loc, typecheck-fast, test-fast) is the gate; coverage
  stays ≥80%.

## 7. Snapshot rebuild (layer C)
- `uv run clingen-link refresh --out clingen_link/data/clingen.sqlite` with the new ETL; re-compress
  to `clingen.sqlite.zst` (zstd), regenerate `clingen.sqlite.sha256`; commit the `.zst`+`.sha256`
  (raw `.sqlite` is gitignored). Verify: dosage count 2026, ≥1 `gene.name` populated, `FANCD1`→BRCA2
  resolves, sample `grch37` non-null, and the live-failing CAIDs now succeed via snapshot + refresh.
- If a refresh is non-deterministic or breaks a drift test, ship layers A+B (all code findings fixed,
  H2/M1 correct on the old bundle) and document that the weekly `data-refresh.yml` realizes
  L2/L3/L5; but the verified network access makes a clean rebuild the expected outcome.

## 8. Out of scope (YAGNI)
- Reverse-DNS `_meta` key renaming (current keys are spec-compliant; renaming churns the shared
  house envelope the assessment praised).
- Live HGNC resolution in the request path (violates the snapshot-first architecture).
- New MCP protocol-RC (`2026-07-28`) features (stateless handshake, any-JSON structuredContent) —
  not final; build to `2025-11-25`.

## 9. Success criteria
All 13 findings resolved; `make ci-local` green; coverage ≥80%; rebuilt bundle shows correct dosage
count, populated names, working `FANCD1` alias, and non-null `grch37`; `refresh=true` returns live
data or safely degrades (never `validation_failed`). Target re-score **>9.5/10**.
