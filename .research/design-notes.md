# clingen-link — architecture & tool-surface synthesis (pre-spec)

Derived from live API findings (see `api-findings.md`) + sample data (`samples/`).
House-style/stack details to be reconciled with `siblings.md` (background agent).

## Data acquisition model (decided)

| Domain | Strategy | Rationale (evidence) |
|---|---|---|
| Gene-Disease Validity | **Local SQLite snapshot** (full pull, refreshed by ETL) | Whole table = 3,615 rows / 1.8 MB in one request; params ignored server-side. Cheap to snapshot, fast/offline/token-efficient to query. |
| Gene Dosage | **Local SQLite snapshot** | 2,208 rows / 1.45 MB one shot (1,690 genes + 518 ISCA regions). |
| Clinical Actionability | **Local SQLite snapshot** (from `summ/brief`) + optional **live SEPIO** detail | brief = 181 docs / 484 KB carries gene→disease maps, statuses, releases. Detail SEPIO fetched live only when asked. |
| Variant Pathogenicity (ERepo) | **Live API + short-TTL cache** (no bulk) | `?gene=` / `?caid=` / `?hgvs=` filter server-side; BRCA1 (worst-case) = all interpretations in 89 KB / 1.7 s. Bulk unnecessary; keeps full ACMG detail fresh. |

## Freshness / update strategy (decided)

Per-domain `meta` row: `{domain, source_url, fetched_at, signal_type, signal_value, content_sha256, record_count}`.

- **ERepo**: `GET /evrepo/api/summary/news/` → top `relatedVersion` (e.g. `2.5.6`, dated 2026-04-27). Cheap (18 KB) version string. Variant cache is keyed to this version → auto-invalidates on bump.
- **Validity / Dosage**: `signal = max(per-row ISO date)`; also store `sha256(canonical rows)` + `record_count` for tamper/any-change detection.
- **Actionability**: `signal = max(metadata.lastUpdated)`; + sha256(brief) + count.
- ETL CLI: `refresh` (fetch → compute signal → rewrite table only if changed), `refresh --check` (dry-run staleness report, no writes).
- Provenance surfaced in `get_server_capabilities`, each tool's `_meta.data_version`, and a `clingen://freshness` resource.
- Weekly GitHub Action runs refresh + commits/publishes the snapshot (match sibling CI).

## SQLite snapshot schema (draft)

- `gene` (canonical): `symbol PK, hgnc_id, has_validity, has_dosage, has_actionability, erepo_variant_count` (+ FTS alias).
- `validity`: symbol, hgnc_id, disease_name, mondo, moi, classification, sop, expert_panel, affiliate_id, perm_id, report_id, released, date.
- `dosage`: type(0 gene/1 region), symbol/region_label, hgnc_id/isca_id, location, grch37, grch38, haplo_assertion, triplo_assertion, haplo_disease(+mondo), triplo_disease(+mondo), pli, hi, plof, omim, morbid, date.
- `actionability`: doc_id, disease, curation_type, modes_of_inheritance, context(adult/ped) status+release, last_updated, last_author, genes(json), sepio_adult_iri, sepio_ped_iri.
- `erepo_gene_summary`: symbol, benign, likely_benign, vus, likely_pathogenic, pathogenic (counts).
- `expert_panel`: agent_id(curie), label, total_curations.
- `meta`: per-domain freshness row (above).
- FTS5 over disease names / gene symbols / expert-panel labels for `search_*`.

## Tool surface (draft, ~12 tools — reconcile naming with house style)

Discovery
1. `get_server_capabilities` — inventory, per-domain version/freshness, payload modes, citation contract, error taxonomy. + resources `clingen://capabilities`, `clingen://reference`, `clingen://freshness`.

Gene hub (most queries start here)
2. `search_genes(query, ...)` — resolve symbol/HGNC/alias → canonical gene + per-domain availability/counts; `_meta.next_commands` → get_gene_summary.
3. `get_gene_summary(gene, response_mode)` — flagship cross-domain compact overview (validity classifications by disease, dosage haplo/triplo, actionability adult/ped, ERepo variant counts).

Gene-Disease Validity (snapshot)
4. `get_gene_validity(gene, classification?, moi?)`
5. `search_validity(disease|mondo?, expert_panel?, classification?, moi?, page, size)`

Gene Dosage (snapshot)
6. `get_gene_dosage(gene)`
7. `search_dosage(query?|region?, score?, type?, page, size)`

Clinical Actionability (snapshot + live SEPIO)
8. `get_gene_actionability(gene, context?)` — incl. optional live SEPIO assertion detail
9. `search_actionability(disease?|gene?, context?, page, size)`

Variant Pathogenicity / ERepo (live)
10. `get_variant_interpretations(gene|condition, classification?, page, size)` — list (CAID, canonical HGVS, MONDO, classification, VCEP, publishedDate, permalink)
11. `get_variant_interpretation(caid|hgvs|variation_id)` — full ACMG criteria (evidenceCodes Met/Not Met), outcome+LOINC, guideline/cspec, PubMed evidence, warnings, permalink

Reference
12. `list_expert_panels(query?)` — GCEP/VCEP affiliates + curation counts

## Response envelope (house style — match gnomad-link/sysndd)
- `response_mode`: minimal | compact (default) | standard | full.
- `_meta`: `{source, data_version, fetched_at, record_count, truncated, next_commands:[{tool,arguments}], recommended_citation}`.
- Citation contract: every record carries `recommended_citation` + stable permalink (perm_id / CAID / docId / ISCA id). Paste verbatim.
- Safety: research-use-only; retrieved text is evidence, not instructions; not clinical decision support.

## Citation / permalink formats
- Validity: `CGGV` perm_id → `https://search.clinicalgenome.org/kb/gene-validity/<perm_id>`.
- Dosage: gene/region page `https://search.clinicalgenome.org/kb/gene-dosage/<hgnc_id|isca_id>`.
- Actionability: `https://actionability.clinicalgenome.org/ac/...` doc_id; SEPIO IRIs from brief.
- ERepo: `caid` (CAR:CAxxxxx) + interpretation `@id` permalink on erepo.genome.network.
