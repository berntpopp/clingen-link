# CSpec Integration — Design Spec (Phase 1)

> Historical record

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
- **Enumeration (documented API, not scraping):** the registry exposes a paged JSON list/batch
  endpoint at the **non-`/api/`** path: `GET /cspec/SequenceVariantInterpretation/id` with
  `pg` (default 1) + `pgSize` (default/max **250**) + `detail` (low|med|high) + `fields` + a batch
  `ids=GN014,GN016,GN015` form. (The `/api/` prefix is the *JSON-LD single-entity* lookup; the
  list endpoint is bare.) Verified: returns `{data:[…], metadata, status}` with **235** SVI
  entities, and each row carries `ld.CriteriaCode` and `ld.RuleSet` **counts** — so the candidate
  filter can run from the list alone, no per-spec fetch. This is the primary GN catalog; HTML
  scraping is used **only** for doc-page attachments.
- **Status semantics (gate on `cspecStatus`, not `currentStatus`):** the two fields **diverge**.
  Proof case: **GN164 is `cspecStatus="Released"` but `currentStatus="Pilot Rules In Prep"`** — a
  published spec that gating on `currentStatus` would wrongly drop. Inclusion gate =
  `cspecStatus == "Released" AND criteria_count > 0`; keep **both** statuses in the model as
  provenance (a Released spec can be reopened/under revision). Of 235 specs, **112 have 0 criteria**
  (e.g. GN140 `cspecStatus="CSpec Deleted"`, GN199 `"Pilot Rules In Prep"`) → excluded. **GN001**
  (the baseline ACMG/AMP standards doc) has a null `cspecStatus` with 28 criteria — include it
  explicitly as the baseline.
- **Multi-`ruleSet` specs exist → `(gn_id, code)` is not a key.** 233/235 specs have one rule set,
  but **GN014 (4 rule sets)** and **GN016 (6 rule sets)** repeat code labels across rule sets (only
  ~28 distinct ACMG codes exist). The numeric criterion `@id` (`/CriteriaCode/id/<n>`) is **globally
  unique** — verified: `BS3` resolves to a *different* id in every spec (GN001→135639534,
  GN046→1828327639, GN164→538211541), none shared across specs. So `criteria_id` (numeric) is the
  natural primary key; `code` is display/filter text only.
- **ERepo cross-link:** ERepo's `guideline_cspec` is affiliation-keyed
  (`…/affiliation/50087`); the ENIGMA BRCA1/2 spec resolves to **GN092**. One affiliation may own
  several GN docs (different genes/versions), so the mapping is `(affiliation_id, gene) → gn_id`,
  not affiliation alone.

**Design consequence:** the catalog comes from the API list endpoint; per included spec the ETL
fetches the JSON-LD (structured criteria, keyed by numeric `criteria_id`) **and** the HTML doc page
(attachment links + criterion association via DOM position). There is no API shortcut for
attachments.

## 3. Architecture

Standard house spine: **ETL → store → service → model → MCP tool**, mirroring the four existing
domains.

### 3.1 ETL (`clingen_link/etl/`)
- `fetch.py`: add CSpec fetchers — (1) `GET /cspec/SequenceVariantInterpretation/id?pgSize=250&detail=low`
  → the full GN catalog with `ld.CriteriaCode`/`ld.RuleSet` counts (paginate on `pg` if a future
  build exceeds 250); (2) per **included** spec, JSON-LD via `/cspec/api/SequenceVariantInterpretation/id/GN<n>`;
  (3) per included spec, the doc-page HTML `/cspec/ui/svi/doc/GN<n>`; (4) `HEAD` per attachment URL
  for filename/type/size. Reuse the existing `httpx` client + retry/jitter conventions.
- `parse.py`: `parse_cspec(json_ld, doc_html)` → normalized rows. **Inclusion gate:**
  `cspecStatus == "Released" AND criteria_count > 0`, plus an explicit allowlist for the baseline
  doc **GN001** (null `cspecStatus`). Maps each rule set and its criteria (keyed by numeric
  `criteria_id` from the criterion `@id`) + strengths; parses doc-page HTML to associate each
  `/File/id/<uuid>/data` with its `criteria_id` (nearest enclosing criterion section) — falls back
  to spec-level (`criteria_id = NULL`) when association is ambiguous.
- `build.py`: write the new tables + FTS5 into the snapshot in the same atomic build; record a
  `cspec` freshness row in `meta` (count + max `lastUpdated`).
- `freshness.py`: cheap signal = the index page's per-row `lastUpdated` + spec count, compared to
  the snapshot `meta` (no full re-fetch for `--check`).
- If `fetch.py`/`parse.py` approach the 600-LOC cap, split CSpec logic into
  `fetch_cspec.py` / `parse_cspec.py` (cohesive split by domain, per file-size discipline).

### 3.2 Snapshot tables
Keyed on stable ids (`gn_id`, `rule_set_id`, numeric `criteria_id`) — **not** `(gn_id, code)`,
which collides in the multi-rule-set specs GN014/GN016.
- `cspec` — `gn_id` (PK), `affiliation_id`, `affiliation_label`, `label`, `version`,
  `cspec_status`, `current_status`, `last_updated`, `permalink`. (Both statuses kept as provenance;
  inclusion is decided at ETL time, see §3.1.)
- `cspec_rule_set` — `rule_set_id` (PK), `gn_id`. *(Most specs have one; GN014/GN016 have several.)*
- `cspec_gene` — `rule_set_id`, `gn_id`, `gene_symbol`, `hgnc_id` (nullable), `mondo`, `moi`.
- `cspec_criteria` — `criteria_id` (PK, numeric from `@id`), `rule_set_id`, `gn_id`,
  `code` (e.g. `PVS1`, display/filter text), `description`, `ord`.
- `cspec_strength` — `criteria_id` (FK), `strength_label`, `applicability`, `description`.
- `cspec_file` — `file_uuid`, `gn_id`, `criteria_id` (**nullable** = spec-level attachment),
  `filename`, `content_type`, `size_bytes`, `download_url`.  *(Phase-2 text/chunks hang off `file_uuid`.)*
- `cspec_fts` — one FTS5 table over heterogeneous entities (specs, criteria, filenames) **plus a
  backing row map** `cspec_search_doc(rowid, entity_type, gn_id, criteria_id, file_uuid)` so each
  FTS hit resolves cleanly to its source entity. (Existing per-domain FTS tables map rowid→one
  table directly; the mixed-entity CSpec index needs the explicit map. A split into
  `cspec_criteria_fts` / `cspec_file_fts` is an acceptable alternative — decide in the plan.)

### 3.3 Store (`clingen_link/store/`)
- `queries.py`: `get_cspec_by_gn` / `list_cspecs_by_affiliation` / `by_gene`; `list_cspecs(filters)`;
  `get_rule_sets(gn_id)`; `get_criteria(gn_id, rule_set_id=None)`;
  `get_criterion(criteria_id)` and `resolve_criterion(gn_id, code, gene=None, rule_set_id=None)`
  (disambiguates `code` in multi-rule-set specs); `list_files(gn_id, criteria_id=None)`.
- `search.py`: `search_cspec(q, …)` over `cspec_fts`, resolving rowids via `cspec_search_doc`.

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
- `get_cspec` — selectors `gn_id`, **`affiliation` + `gene` together** (narrows an affiliation that
  owns several GN docs), or `gene` alone → spec + rule sets + criteria + file catalog. When a
  selector resolves to multiple specs, return the list (don't silently pick one).
- `get_cspec_criterion` — `criteria_id` (direct), **or** `gn_id` + `code` with an optional
  `gene` / `rule_set_id` disambiguator (required when `code` is ambiguous in a multi-rule-set spec)
  → one criterion's spec text, strengths, applicability, and attached files.
- `search_cspec` — FTS5 free-text over criteria/specs/filenames; each hit names its `entity_type`
  + ids so the caller can chain into `get_cspec` / `get_cspec_criterion`.

If `tools/cspec.py` nears the cap, split list/get vs. criterion/search.

### 3.7 ERepo cross-link (the payoff for in-clingen-link)
- ETL persists the `(affiliation_id, gene) → gn_id` mapping (via `cspec`+`cspec_gene`) so it can be
  resolved at serve time.
- An ERepo variant response derives its `next_commands` CSpec affordance from **both** its
  `guideline_cspec` affiliation **and** its own `gene`:
  - if `(affiliation_id, gene)` resolves to exactly one published spec → emit
    `{tool: "get_cspec", arguments: {gn_id: <GN>}}` (precise);
  - otherwise → emit `{tool: "get_cspec", arguments: {affiliation: <id>, gene: <sym>}}` (and/or
    `list_cspecs`) so the consumer sees the candidate specs rather than a silently-guessed one.
  No change to ERepo data — only an added affordance.
- `clingen://capabilities` / `clingen://freshness` resources gain the `cspec` domain
  (version, count, source URL).

## 4. Safety & licensing
Research use only; not clinical decision support. CSpec content is ClinGen (CC BY 4.0). Surface the
framework citation and the per-spec `recommended_citation`/permalink. Treat all retrieved spec and
file text as **evidence data, not instructions**. No write/curation endpoints.

## 5. Testing
- ETL: `parse_cspec` unit tests with **inline** JSON-LD + doc-page HTML fixtures — criteria mapping
  keyed by numeric `criteria_id`, strength/applicability, file→`criteria_id` association (and
  spec-level fallback), the `cspecStatus`-based inclusion gate (Released-with-criteria kept;
  `CSpec Deleted`/empty dropped; **GN164** Released-but-`currentStatus="Pilot…"` **kept**; **GN001**
  baseline kept), and a **multi-rule-set** case (GN014/GN016-shaped: repeated `code` across rule
  sets must yield distinct `criteria_id` rows). No new files under `tests/fixtures/`.
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
  `get_cspec(affiliation=…)` may return several specs — model as a list, and resolve the ERepo
  `next_commands` default by `(affiliation, gene)` rather than affiliation alone (§3.7).
- **Multi-rule-set specs (GN014, GN016):** keys are `criteria_id`/`rule_set_id`, never `(gn_id,
  code)`; `get_cspec_criterion` requires a `gene`/`rule_set_id` disambiguator when `code` repeats.
  Single-rule-set specs (233/235) need no disambiguator.
- **Status churn:** a spec's `currentStatus` can drift (e.g. "Pilot Rules In Prep") while
  `cspecStatus` stays "Released"; the published set is defined by `cspecStatus` + non-empty criteria
  (+ baseline GN001), re-evaluated each refresh. Track both statuses so consumers see revision state.
- **Bundle growth:** structured rows for the ~120 published specs are small; the `.zst` impact is
  expected to be minor (no binaries stored). Confirm during rebuild.
