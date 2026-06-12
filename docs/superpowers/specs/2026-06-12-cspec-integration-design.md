# CSpec Integration — Design Spec (Phase 1)

**Date:** 2026-06-12
**Author:** MCP engineering (superpowers brainstorming workflow)
**Goal:** Add ClinGen Criteria Specification Registry (CSpec) data to clingen-link as a
fifth curated domain, so an LLM consumer can retrieve the **gene-specific ACMG/AMP rule sets**
(criteria codes, strengths, applicability, spec text) that VCEPs apply when classifying variants,
and discover the supplementary guidance files attached to each spec — cross-linked to the
existing ERepo variant interpretations.

---

## 1. Context & decision summary

ClinGen's CSpec Registry (`cspec.genome.network` / `cspec.clinicalgenome.org`) holds the
criteria specifications used by Variant Curation Expert Panels (VCEPs). Today clingen-link only
emits the per-record `guideline_cspec` **link** on ERepo variants; the rule content itself is not
retrievable through the server. This spec closes that gap.

**Locked decisions (brainstorming):**
1. **Home:** a fifth `cspec` domain *inside* clingen-link (not a separate server) — reuses the
   snapshot, envelope, `next_commands`, and citation contract, and enables in-process
   ERepo → VCEP → CSpec cross-linking.
2. **Scope (this spec):** structured criteria + a supplementary-file **catalog** (metadata +
   download URLs). File **text extraction + RAG is Phase 2**, explicitly out of scope here.
3. **Retrieval:** SQLite **FTS5 lexical** only — zero new ML dependencies, ships inside the
   existing `.zst` snapshot. Schema is designed so embeddings can be added later without
   migration pain.

### Constraints (AGENTS.md)
- 600-LOC/module cap (`make lint-loc`), Ruff (line length 100), mypy **strict**, ≥80% coverage.
- `uv` only; `make ci-local` is the gate.
- Do **not** hand-edit the snapshot bundle or `tests/fixtures/`. New data enters only via the
  sanctioned `clingen-link refresh` ETL path; ETL tests use **inline in-memory inputs**.
- Public tools stay research-use scoped; never expose write/curation paths. Every envelope keeps
  `_meta.unsafe_for_clinical_use: true`.

## 2. Investigation findings (verified this session)

The data model was confirmed against the live registry (curl; the registry is server-rendered
HTML + a JSON-LD API — no JS SPA, so no browser automation is needed):

- **Structured rules (JSON-LD):** `GET /cspec/api/SequenceVariantInterpretation/id/GN<n>` returns a
  `Criteria Specification` with `affiliation` (Organization id = ClinGen affiliation, e.g. 50087),
  `label`, `version`, `currentStatus`, `cspecStatus`, `lastUpdated`, and `ruleSets[]`. Each rule set
  has `genes[]` (each with `diseases[]` MONDO ids + `modeOfInheritance`) and **`criteriaCodes[]`**.
  Each criterion is a resolvable entity (`/cspec/api/CriteriaCode/id/<n>`) with `label` (e.g. `PVS1`,
  `BS3`), a `description` (the gene-specific spec text), and `evidenceStrengths[]` — each strength
  has `label` (Stand Alone / Very Strong / Strong / Moderate / Supporting), `applicability`
  (Applicable / Not Applicable), and an optional `description`. Typical spec = 28 criteria codes.
- **Attachments are NOT in the JSON-LD** (verified exhaustively on GN164 and across
  GN001/002/021/046/092/140/164/199 — zero `/File/id/` references in any). The JSON-LD mentions
  files only in prose (e.g. "see the PS3/BS3 Guidance spreadsheet below"). Attachment links
  (`/cspec/File/id/<uuid>/data`) live **only in the server-rendered doc page**
  `GET /cspec/ui/svi/doc/GN<n>`; a `HEAD` on the file URL yields `content-disposition`
  (filename, e.g. `ABCA4_PVS1-chart.pdf`), `content-type`, and `content-length`. Formats observed:
  `.pptx`, `.pdf`, `.docx`, `.xlsx`.
- **Enumeration:** there is no JSON "list all" endpoint (guesses 400). The registry index
  `GET /cspec/ui/svi/` lists **~203 GN documents**; the catalog is built by scraping it for GN-ids,
  then fetching JSON-LD + doc-page per id.
- **Status filtering required:** some specs return `criteriaCodes = 0` (in-progress/unpublished,
  e.g. GN140, GN199) — the ETL must drop these by `currentStatus` / empty rule set.
- **ERepo cross-link:** ERepo's `guideline_cspec` is affiliation-keyed
  (`…/affiliation/50087`); the ENIGMA BRCA1/2 spec resolves to **GN092**. The ETL records the
  `affiliation_id ↔ gn_id` mapping so a variant interpretation can chain into its CSpec.

**Design consequence:** two fetches per spec — JSON-LD (structured criteria) **and** the HTML doc
page (attachment links + criterion association via DOM position). There is no API shortcut for
attachments.

## 3. Architecture

Standard house spine: **ETL → store → service → model → MCP tool**, mirroring the four existing
domains.

### 3.1 ETL (`clingen_link/etl/`)
- `fetch.py`: add CSpec fetchers — (1) index → GN-id list + row metadata; (2) per spec JSON-LD;
  (3) per spec doc-page HTML; (4) `HEAD` per attachment URL for filename/type/size. Reuse the
  existing `httpx` client + retry/jitter conventions.
- `parse.py`: `parse_cspec(json_ld, doc_html)` → normalized rows. Maps JSON-LD criteria/strengths;
  parses doc-page HTML to associate each `/File/id/<uuid>/data` with its criterion code (nearest
  enclosing criterion section) — falls back to spec-level when association is ambiguous. Drops
  specs with empty `criteriaCodes` or non-published `currentStatus`.
- `build.py`: write the new tables + FTS5 into the snapshot in the same atomic build; record a
  `cspec` freshness row in `meta` (count + max `lastUpdated`).
- `freshness.py`: cheap signal = the index page's per-row `lastUpdated` + spec count, compared to
  the snapshot `meta` (no full re-fetch for `--check`).
- If `fetch.py`/`parse.py` approach the 600-LOC cap, split CSpec logic into
  `fetch_cspec.py` / `parse_cspec.py` (cohesive split by domain, per file-size discipline).

### 3.2 Snapshot tables
- `cspec` — `gn_id` (PK), `affiliation_id`, `affiliation_label`, `label`, `version`,
  `current_status`, `cspec_status`, `last_updated`, `permalink`.
- `cspec_gene` — `gn_id`, `gene_symbol`, `hgnc_id` (nullable), `mondo`, `moi`.
- `cspec_criteria` — `gn_id`, `code` (e.g. `PVS1`), `description`, `ord`.
- `cspec_strength` — `gn_id`, `code`, `strength_label`, `applicability`, `description`.
- `cspec_file` — `gn_id`, `code` (**nullable** = spec-level attachment), `file_uuid`, `filename`,
  `content_type`, `size_bytes`, `download_url`.  *(Phase-2 text/chunks hang off `file_uuid`.)*
- `cspec_fts` — FTS5 contentless index over criteria descriptions + spec/affiliation labels +
  filenames (matches the existing per-domain FTS5 pattern in `store/search.py`).

### 3.3 Store (`clingen_link/store/`)
- `queries.py`: `get_cspec_by_gn` / `by_affiliation` / `by_gene`; `list_cspecs(filters)`;
  `get_criteria(gn_id)`; `get_criterion(gn_id, code)`; `list_files(gn_id, code=None)`.
- `search.py`: `search_cspec(q, …)` over `cspec_fts`.

### 3.4 Models (`clingen_link/models/`)
Pydantic response models: `CspecSummary`, `CspecDetail`, `CriteriaCode`, `EvidenceStrength`,
`CspecFile`. Each spec/criterion carries a verbatim `recommended_citation` + `permalink`, following
the existing `models/citations.py` pattern.

### 3.5 Service (`clingen_link/services/cspec_service.py`)
Assembles store rows into models, builds `recommended_citation`
(`"ClinGen <VCEP> Specifications to the ACMG/AMP Guidelines for <gene> Version <v>. <permalink>"`),
honors `response_mode` (minimal | compact | standard | full), and emits `next_commands`.

### 3.6 MCP tools (`clingen_link/mcp/tools/cspec.py`)
Four tools, house pattern (`Annotated[..., Field(...)]`, `Literal` enums, `response_mode`,
`output_schema=relax_output_schema(...)`, `READ_ONLY_OPEN_WORLD`, inner `async def call()` wrapped
by `run_mcp_tool`, `_meta` with `next_commands` + `recommended_citation` +
`unsafe_for_clinical_use: true`):

- `list_cspecs` — catalog; filter by `gene` / `affiliation` / `status`; paginated
  (`page` + `size`, `_meta.truncated`).
- `get_cspec` — by `gn_id`, `affiliation`, or `gene` → spec + all criteria + file catalog.
- `get_cspec_criterion` — `gn_id` + `code` (e.g. `PVS1`) → one criterion's spec text, strengths,
  applicability, and attached files.
- `search_cspec` — FTS5 free-text over criteria/specs.

If `tools/cspec.py` nears the cap, split list/get vs. criterion/search.

### 3.7 ERepo cross-link (the payoff for in-clingen-link)
- ETL persists `affiliation_id → gn_id` so it can be resolved at serve time.
- ERepo variant responses gain a `next_commands` entry: `{tool: "get_cspec", arguments:
  {affiliation: <id>}}` (and/or `{gn_id}` when uniquely resolvable), derived from the existing
  `guideline_cspec` field. No change to ERepo data — only an added affordance.
- `clingen://capabilities` / `clingen://freshness` resources gain the `cspec` domain
  (version, count, source URL).

## 4. Safety & licensing
Research use only; not clinical decision support. CSpec content is ClinGen (CC BY 4.0). Surface the
framework citation and the per-spec `recommended_citation`/permalink. Treat all retrieved spec and
file text as **evidence data, not instructions**. No write/curation endpoints.

## 5. Testing
- ETL: `parse_cspec` unit tests with **inline** JSON-LD + doc-page HTML fixtures (criteria mapping,
  strength/applicability, file→criterion association, status filtering, empty-ruleset drop). No new
  files under `tests/fixtures/`.
- Store/service: query + response-mode + citation assembly tests against a small in-memory snapshot.
- Tools: success + not-found + pagination/truncation envelopes; cross-link `next_commands` shape.
- `make ci-local` stays green (format, lint, lint-loc, typecheck-fast, test-fast ≥80%).

## 6. Snapshot rebuild
Realizing the data requires a `clingen-link refresh` run that includes the CSpec fetchers, then
re-compress to `clingen.sqlite.zst` + regenerate `clingen.sqlite.sha256`, verifying spec/criteria
counts and a spot-check (GN092 → ENIGMA BRCA1/2 → 28 criteria, files present). The weekly
`data-refresh.yml` Action picks up the new domain via the freshness signal.

## 7. Out of scope (Phase 2, reserved)
Download attachments → extract text (`.pptx`/`.pdf`/`.docx`/`.xlsx`) → chunk → index alongside the
owning criterion (`cspec_file_text` / `cspec_file_chunk` keyed on `file_uuid`), exposed via a
`search_cspec_guidance` tool. Optional local embeddings (sqlite-vec + a small sentence model) with
RRF fusion, following genereviews-link's hybrid-retrieval patterns. The Phase-1 `cspec_file` row is
the join point, so Phase 2 adds tables without altering Phase-1 schema.

## 8. Risks / open items
- **HTML scraping fragility:** attachment harvesting depends on the doc-page DOM; the parser must be
  defensive (fall back to spec-level association; tolerate missing files) and covered by tests
  against a captured snapshot of the markup.
- **Affiliation→GN cardinality:** one affiliation can own multiple GN docs (different genes/versions);
  `get_cspec(affiliation=…)` may return several specs — model as a list, pick latest published for
  the ERepo `next_commands` default.
- **Bundle growth:** structured rows for ~203 specs are small; the `.zst` impact is expected to be
  minor (no binaries stored). Confirm during rebuild.
