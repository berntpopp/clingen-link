# clingen-link MCP — Assessment & Test Report

**Date:** 2026-06-12
**Evaluator:** LLM consumer / senior MCP tester (Claude)
**Server under test:** `clingen-link` v0.1.0 (MCP protocol `2025-11-25`)
**Snapshot:** validity `2026-06-09`, actionability `2026-05-20`, ERepo `2.5.6`, dosage (GRCh38 TSVs)
**Method:** Live exercise of all 13 tools + 6 resources via the MCP interface (~40 calls): happy path, every filter, pagination/truncation, all four `response_mode` tiers, both live-fetch paths, and error/edge inputs. No source code was read; findings are black-box, reproduced from tool responses.

> **Scope note:** This is a research-use server (`unsafe_for_clinical_use: true` on every envelope). Nothing here is clinical guidance; this report evaluates the *engineering* of the MCP surface.

---

## Executive summary

clingen-link is a solid, well-architected server with an excellent read path. Its standout strengths are **observability** (diagnostics + schema-drift watch + per-response provenance) and **discoverability** (capabilities doc, 6 resources, `next_commands` chaining). Black-box testing surfaced **one broken advertised feature, one wrong data-count that misrepresents an entire domain, and a gene-resolution correctness bug**, plus several consistency and token-efficiency defects.

**Overall: 8/10.** Production-capable with caveats — the snapshot read path is trustworthy; the live `refresh` path is not, and a few issues will actively mislead an LLM consumer. The blocking items (H1–H3) are small, well-localized fixes.

---

# Part 1 — LLM-consumer UX assessment

Rating each dimension an MCP should excel at, from the perspective of the model calling the tools.

| Dimension | Score | Basis |
|---|---|---|
| Discoverability | 9 | `get_server_capabilities` + 6 `clingen://` resources + `next_commands` chaining + per-tool token hints + `capabilities_version` content hash |
| Observability | 9 | `get_clingen_diagnostics` with error history **and schema-drift detection**; `data_version` + `source_url` + `fetched_at` on every response |
| Speed (architecture) | 8 | Snapshot-backed read-only path; live SEPIO only on explicit opt-in |
| Error handling | 7 | Typed taxonomy, `recovery_action`, human-readable recovery text, `next_commands` on error — but one circular fallback + an alias gap (and see H1/M-class issues in Part 2) |
| Token efficiency | 6 | Four `response_mode` tiers + published cost hints, but heavy envelope duplication and an untrimmed HGVS array |
| Consistency | 9 | Same canonical envelope across all 13 tools |
| **Overall** | **8** | Well-architected "house-style" server; strongest at observability/discoverability, weakest at payload dedup |

### Strengths (validated)

- **Discoverability.** Capabilities is a single ~4 kB call exposing datasets, freshness, recommended workflows, the tool list, *per-tool token-cost hints*, the error-code enum, parameter conventions, and a `capabilities_version` content hash for warm-client cache skipping. Six resources annotated `audience: ["assistant"]`. Every response carries `_meta.next_commands` as ready-to-call `{tool, arguments}` — no guessing the next step.
- **Observability.** `get_clingen_diagnostics` returns `recent_errors`, `recent_error_count`, and `recent_schema_drift` (active upstream-shape watching). Every payload embeds `data_version` with upstream `source_url` and fetch timestamp.
- **Speed architecture.** Common path served from a bundled read-only SQLite snapshot; only single-record SEPIO drill-down (`include_detail=true` / `refresh=true`) touches the network. (Latency was not benchmarked through the tool layer — this is an architectural rating.)

### Weaknesses (the short list)

1. **Envelope duplication** is the biggest token sink — `recommended_citation` repeated 3–4× per response; full `data_version` re-serialized every call.
2. **Alias resolution gap** — `FANCD1` (official HGNC alias for BRCA2) returns `not_found` though aliases are advertised.
3. **Circular not_found fallback** — `search_genes` failure suggests re-calling `search_genes` with the identical failing query.
4. **`minimal` mode is mildly confusing** — returns `records: []` with a non-zero `total`.

> Harness caveat (not the server's fault): in this client, tools arrive deferred, so each schema must be loaded before first use. Precise tool descriptions made the matching painless.

---

# Part 2 — Senior-tester full test report

### Verdict

Production-capable with caveats. The snapshot read path is trustworthy; the live `refresh` path is not. Two issues (H1, H2) will actively mislead an LLM. All blocking items are small, well-localized fixes.

## High-severity findings

### H1 — `get_variant_interpretation refresh=true` is broken (live ERepo path unusable)
Reproduced on two valid CAIDs (`CA003681`, `CA003305`): both return `error_code: validation_failed`, message `"Invalid input: ValidationError"`. The **snapshot** path for those exact same IDs returns full data. Two compounding problems:
- (a) the advertised "bypass the snapshot and fetch the live SEPIO interpretation" feature fails outright;
- (b) it is misclassified as a *user* error (`recovery_action: reformulate_input`, "check the tool schema"), so an autonomous LLM will wrongly conclude its CAID is malformed and burn turns reformulating a valid identifier.

**Fix:** repair the live fetch/parse; on live failure, fall back to snapshot with `source: "snapshot"` + a notice, or surface `upstream_unavailable`. Never return `validation_failed` for valid inputs. **Practical workaround today:** do not use `refresh=true`; the snapshot already serves full ACMG detail.

### H2 — Dosage `record_count: 2` is wrong everywhere; real count is 2,026
`search_dosage` reports `total: 2026` (1,513 `gene` + 513 `region`, both confirmed via `record_type` filters), but `get_server_capabilities`, `get_clingen_diagnostics`, and **every** response's `_meta.data_version.dosage.record_count` report **2**. The count is being derived from the two source TSV filenames embedded in the `version` string, not from row count. This breaks the freshness/observability contract for one of four domains — an LLM reading capabilities to decide whether dosage is worth querying would conclude it is essentially empty.

**Fix:** set `record_count = COUNT(*)` of the dosage table; keep the ETag string in `version` only.

### H3 — HGNC-ID resolution uses substring matching instead of exact match
- `search_genes("HGNC:1100")` → BRCA1 (correct) **plus** spurious `SLC2A1 (HGNC:11005)`.
- `search_genes("HGNC:1101")` → BRCA2 **plus** `SLC30A2 (11013)` + `SLC34A1 (11019)`.
- `search_genes("HGNC:11")` → **25 candidates, `resolved_symbol: null`** (matches every id containing "11": 118, 119, 1100, 11110, …).

HGNC IDs are unique keys and must be exact-match lookups; here a canonical ID returns polluted candidate sets and short IDs fail to resolve. Gene resolution is the entry point of the whole workflow.

**Fix:** when input matches `^HGNC:\d+$`, do equality on `hgnc_id`; never route it through FTS/LIKE.

## Medium-severity findings

| # | Finding | Evidence | Fix |
|---|---|---|---|
| M1 | **Unsanitized HTML in data + citations.** `disease_name` contains raw markup that propagates verbatim into `recommended_citation`. | `search_validity(disease="cardiomyopathy")` → TMPO record: `" familial isolated dilated cardiomyopathy <span class=\"badge…\">Obsolete Term</span>"` | Strip tags + collapse whitespace in ETL; expose obsolescence as a structured `disease_obsolete: true` flag. Violates the "paste citation verbatim" contract and is an unsanitized-passthrough surface. |
| M2 | **`compact` never trims the ~51-element `hgvs` array** in ERepo tools — dominates payload (~2.5 kB/variant; ~60 kB at default `size=25`). | Every `get_variant_interpretation(s)` result, all modes | In minimal/compact return only canonical genomic (GRCh38) + MANE transcript + protein; gate full `hgvs[]` behind standard/full. **Biggest token win in the server.** |
| M3 | **`response_mode` is inconsistent and partly inverted.** `minimal`: validity/summary → counts-only; **dosage → records *with* null fields, i.e. more verbose than `compact`.** | `get_gene_dosage(BRCA1, minimal)` adds `hgnc_id:null, isca_id:null, grch37:null, triplo_mondo:null` that compact omits | Enforce minimal ⊆ compact ⊆ standard ⊆ full uniformly; document per-tool what each tier drops. |
| M4 | **Envelope duplication at scale.** `recommended_citation` repeated 3–4×; full `data_version` re-serialized every call; multi-domain tools embed **all four** domain version-blocks even when one domain was queried. | `search_genes`, `get_gene_summary`, `list_expert_panels` `_meta` | Keep citation per-record only; emit `data_version` once, scoped to the domain(s) used. |
| M5 | **"Gene exists but no records in this domain" handled two different ways.** | `get_gene_actionability(NAA10)` → `not_found` **error**, but `get_variant_interpretations(SLC2A1)` (erepo=0) and `get_gene_validity(BRCA1, classification=Limited)` → `success, total:0`. | For a *resolvable* gene with no records in a domain, return `success + total:0`; reserve `not_found` for genes absent from the snapshot entirely (e.g. `ZZZ123`). |

## Low-severity findings

- **L1 — Truncation filter-echo drops `expert_panel`.** `get_variant_interpretations(expert_panel="ENIGMA")` truncates with `filter:{}` (gene/condition/classification *are* echoed). Breaks `to_restore` reproducibility for that one filter.
- **L2 — `name` (gene full name) is always `null`** across `search_genes`/`get_gene_summary`. Field present, never populated — populating from HGNC would also enable the alias fix (L3).
- **L3 — Alias gap + circular fallback.** `FANCD1`→BRCA2 (official alias) returns `not_found`; the `search_genes` not_found envelope's `next_commands[0]` re-suggests `search_genes` with the *identical failing query*.
- **L4 — `get_gene_summary` citation permalink is non-specific** (`…/kb/genes/` with no gene appended).
- **L5 — Doc overpromise:** `get_gene_dosage` advertises "GRCh37/GRCh38 coordinates" but `grch37` is always `null` (only the GRCh38 TSV is ingested).

## Per-tool result matrix

| Tool | Result |
|---|---|
| `get_server_capabilities` | ✅ (ships the wrong dosage count — H2) |
| `get_clingen_diagnostics` | ✅ (ships the wrong dosage count — H2) |
| `search_genes` | ⚠️ HGNC substring match (H3), alias gap (L3), `name` null (L2) |
| `get_gene_summary` | ⚠️ envelope dup (M4), non-specific citation (L4) |
| `get_gene_validity` | ✅ clean (compact/minimal/full, filters, empty-shaping all correct) |
| `search_validity` | ⚠️ HTML in data (M1); pagination/filters ✅ |
| `get_gene_dosage` | ⚠️ minimal inverted (M3), grch37 null (L5); data correct |
| `search_dosage` | ✅ filters/score/record_type/pagination all correct |
| `get_gene_actionability` | ⚠️ no-data → not_found inconsistency (M5); live `include_detail` ✅ |
| `search_actionability` | ✅ (incl. `assertion` post-filter) |
| `get_variant_interpretations` | ⚠️ hgvs bloat (M2), filter-echo gap (L1); filters/pagination ✅ |
| `get_variant_interpretation` | ❌ `refresh=true` broken (H1); snapshot lookups by caid/hgvs/clinvar all ✅ |
| `list_expert_panels` | ✅ (59 panels, counts correct) |

## What's genuinely solid (keep)

- Snapshot architecture is fast; opt-in live actionability (`include_detail=true`) correctly fetched a freshly-produced SEPIO document.
- Variant **lookup is robust**: alternate-transcript HGVS (`ENST…:c.5509T>G`), ClinVar VariationID, and CAID all resolve to the same record; gene symbols are case-insensitive (`brca1`).
- **Pagination/truncation** is well-designed (`kind`/`dropped`/`to_disable`/`to_restore`/`filter`); beyond-range page returns a clean empty result, not a crash.
- **Error taxonomy** is mostly clean & typed; `next_commands` present on success and error.
- **Multi-filter** works (gene+classification, condition+classification, record_type+haplo_score).
- **Full variant detail** is excellent — ACMG evidence codes Met/Not-Met plus the calibrated narrative summary.

## Recommended fix order

1. **H1** — fix or safely degrade `refresh=true`, and stop mislabeling server-side failures as `validation_failed`. (Correctness of an advertised feature + prevents LLM error-loops.)
2. **H2** — correct the dosage `record_count`. (One-line ETL fix; restores the freshness contract for a whole domain.)
3. **H3** — exact-match HGNC IDs. (Entry-point correctness for the whole workflow.)
4. **M2 + M4** together — trim the `hgvs` array in compact and dedupe the envelope. (Largest token savings, no behavior change.)
5. **M1, M3, M5** — sanitize HTML, normalize `response_mode`, unify no-data shaping. (Consistency/trust.)

**Sign-off:** the read path earns confidence. I would block a "production / clinical-adjacent research" sign-off only on **H1–H3**, all of which are small, well-localized fixes.

---

## Appendix — test inventory

Representative calls executed (not exhaustive):

- **Discovery/observability:** `get_server_capabilities`, `get_clingen_diagnostics`, `ListMcpResources`, `clingen://reference`.
- **Validity:** `get_gene_validity` BRCA2/NAA10/BRCA1 (compact/minimal/full), `brca1` (case), `classification=Limited` (empty), `search_validity` disease=cardiomyopathy (size=2, minimal, page=999), mondo filter, gene+classification.
- **Dosage:** `get_gene_dosage` BRCA1/TP53/PTEN (compact/minimal), `ZZZ123` (not_found); `search_dosage` unfiltered, record_type=gene (1,513), record_type=region (513), haplo_score=3+gene (415).
- **Actionability:** `get_gene_actionability` BRCA1 Adult/Pediatric, `include_detail=true` (live SEPIO), NAA10 (not_found); `search_actionability` disease=melanoma, gene+assertion post-filter.
- **ERepo:** `get_variant_interpretations` by gene/expert_panel/condition+classification (pagination + filter-echo); `get_variant_interpretation` by caid (compact/full), hgvs (canonical + alternate transcript), clinvar_variation_id, no-id (invalid_input), absent id (not_found), `refresh=true` ×2 (validation_failed).
- **Resolution:** `search_genes` BRCA1/HGNC:1100/HGNC:1101/HGNC:11/FANCD1/NOTAREALGENE.
- **Panels:** `list_expert_panels` (full, 59) + query=cardiomyopathy.
