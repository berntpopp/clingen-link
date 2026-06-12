# clingen-link Assessment Resolution — 2026-06-12

Resolves every finding in [`mcp-assessment-2026-06-12.md`](./mcp-assessment-2026-06-12.md)
(black-box LLM-consumer assessment, scored **8/10**). Work is organized in three layers:
serve-time/pure-code fixes, ETL enhancement, and a sanctioned snapshot rebuild
(`clingen-link refresh`). Design and plan:
[`superpowers/specs/2026-06-12-clingen-link-9.5-hardening-design.md`](./superpowers/specs/2026-06-12-clingen-link-9.5-hardening-design.md),
[`superpowers/plans/2026-06-12-clingen-link-9.5-hardening.md`](./superpowers/plans/2026-06-12-clingen-link-9.5-hardening.md).

## High severity

| # | Finding | Fix | Proof |
|---|---------|-----|-------|
| **H1** | `get_variant_interpretation refresh=true` broken; valid CAID → `validation_failed` | New `services/erepo_live.py` normalizes the live classifications summary (`gene` dict → label, camelCase keys) + best-effort SEPIO enrichment; `get_interpretation` returns `(model, source, notice)` and **degrades to snapshot** on any live failure, surfacing `upstream_unavailable` — never `validation_failed` | `test_erepo_live.py`, `test_services.py::test_refresh_*`, `test_tool_erepo.py::test_refresh_*`; **verified live** against real ERepo (CA003681 → `source=live`, BRCA1 Pathogenic) |
| **H2** | Dosage `record_count: 2` everywhere (real 2,026) | `Store.meta()` recomputes `record_count` from `COUNT(*)` (serve-time, works on any bundle); ETL also records the real dosage count at build | `test_store.py::TestMetaRecordCount`, `test_etl_build.py::test_dosage_meta_count_is_row_count_not_etag_count`; rebuilt bundle meta = 2026 |
| **H3** | HGNC-ID resolution uses substring match | `queries.search_genes` matches `^HGNC:\d+$` by equality on `hgnc_id`, never `LIKE` prefix | `test_queries.py::TestSearchGenesHgncExactMatch` |

## Medium severity

| # | Finding | Fix | Proof |
|---|---------|-----|-------|
| **M1** | Unsanitized HTML in `disease_name` → citations | `etl/sanitize.py` strips tags/entities + collapses whitespace; `disease_obsolete` exposed as a structured flag. Sanitized at the ETL **and** at the model layer (defense-in-depth) | `test_etl_sanitize.py`, `test_etl_parse.py::test_parse_validity_sanitizes_html_and_flags_obsolete`; bundle has 0 HTML labels, 18 obsolete-flagged |
| **M2** | `compact` never trims the ~51-element `hgvs[]` | `mcp/hgvs_select.canonical_hgvs` keeps genomic GRCh38 + MANE + protein in minimal/compact (+ `hgvs_count`); full array only in standard/full | `test_hgvs_select.py`, `test_shaping.py::TestHgvsTrim` |
| **M3** | `response_mode` inconsistent/inverted (dosage minimal > compact) | `shape_record` projects minimal == compact (strict subset lattice); detail tools omit record bodies in minimal | `test_shaping.py::TestResponseModeLattice` (asserts minimal ⊆ compact ⊆ standard ⊆ full for every domain) |
| **M4** | `recommended_citation` repeated 3–4× | `build_meta` no longer emits `_meta.recommended_citation`; the load-bearing copies stay per-record (+ single top-level summary citation) | `test_tool_*.py` assert `recommended_citation not in _meta` |
| **M5** | "Gene exists, no records" handled two ways | `get_gene_dosage` / `get_gene_actionability` return `success + total:0` for a resolvable gene; `not_found` reserved for genes absent from the index | `test_tool_dosage.py`, `test_tool_actionability.py` (`*_is_success_zero`) |

## Low severity

| # | Finding | Fix | Proof |
|---|---------|-----|-------|
| **L1** | Truncation filter-echo drops `expert_panel` | Added `expert_panel` to the ERepo `truncated.filter` | `test_tool_erepo.py::test_truncation_echoes_expert_panel` |
| **L2** | `name` (gene full name) always null | ETL ingests the HGNC complete-set `name`; 99% of genes now named | `test_etl_hgnc.py`, `test_etl_parse.py::test_build_gene_index_applies_hgnc_name_and_alias`; bundle 3484/3501 named |
| **L3** | Alias gap (`FANCD1`) + circular not_found fallback | ETL adds HGNC `alias_symbol`/`prev_symbol` rows (FANCD1→BRCA2); `errors._fallback_for` never re-suggests the identical failing query | `test_etl_hgnc.py`, `test_errors.py::test_not_found_fallback_avoids_circular_recall`, `test_tool_genes.py::test_not_found_fallback_is_not_circular`; bundle resolves FANCD1→BRCA2 |
| **L4** | Gene-summary citation permalink non-specific | `GeneSummary` permalink targets `…/kb/genes/?search={symbol}` | `test_tool_genes.py::test_summary_citation_permalink_is_gene_specific` |
| **L5** | `grch37` always null (doc overpromise) | ETL fetches GRCh37 dosage TSVs + backfills `grch37`; 2026/2026 rows populated | `test_etl_fetch.py::test_fetch_dosage_captures_etags`, `test_etl_parse.py::test_parse_dosage_grch37_backfill` |

## MCP best-practice alignment (protocol 2025-11-25)

The fixes follow current guidance: error taxonomy keyed on **recoverability not blame** (H1 — an
upstream/parse fault surfaces as `upstream_unavailable`, never `invalid_input`); response-mode tiers
as a strict subset lattice and large-array trimming for token economy (M2/M3); no envelope
duplication (M4). Existing strengths kept: `outputSchema` per tool, `READ_ONLY_OPEN_WORLD`
annotations, `_meta.next_commands` chaining, per-record `recommended_citation`, the
`capabilities_version` content hash, and schema-drift observability. Custom `_meta` keys remain
spec-compliant (they avoid the reserved `mcp`/`modelcontextprotocol` prefixes), so no renaming was
needed.

## Verification

`make ci-local` green (format, lint, LOC budget, mypy strict, 306 unit tests). The bundled snapshot
was regenerated via `clingen-link refresh`; the live ERepo `refresh=true` path was verified against
the production endpoint.
