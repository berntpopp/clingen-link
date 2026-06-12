# clingen-link — Design Spec

**Date:** 2026-06-12
**Author:** bernt.popp@charite.de (autonomous build)
**Status:** Approved-for-build (autonomous goal; decisions documented for review)

A Model Context Protocol (MCP) server that grounds gene/disease/variant questions in
**ClinGen** (Clinical Genome Resource) curated evidence across its four data domains.
Follows the `*-link` house style (template: `gnomad-link`).

Research backing this spec: `.research/api-findings.md` (live network capture),
`.research/clingen-data-sources.md` (download/freshness), `.research/siblings.md`
(house style), `.research/design-notes.md` (synthesis).

---

## 1. Purpose & scope

ClinGen publishes four curated datasets, each with its own site/API:

| Domain | What it answers | Records (2026-06-12) |
|---|---|---|
| **Gene-Disease Validity** | Is gene X causal for disease Y? (Definitive…Refuted) | 3,615 assertions |
| **Gene Dosage** | Is gene/region haploinsufficient / triplosensitive? | 1,690 genes + 518 regions |
| **Clinical Actionability** | Is gene-condition X medically actionable (adult/pediatric)? | 181 curations / ~253 assertion rows per protocol |
| **Variant Pathogenicity (ERepo)** | Expert-panel ACMG classification of variant V | ~12,683 interpretations |

**In scope (v1):** read-only retrieval + search across all four domains, a gene-centric
cross-domain overview, ERepo variant ACMG detail, expert-panel reference, a self-contained
local snapshot with an ETL refresh CLI and freshness tracking, the full house-style MCP
envelope, capabilities/resources, and three transports (unified/http/stdio).

**Out of scope (v1):** GeneGraph GraphQL (internal-only per PMC12001867); semantic/vector
search (data is structured tabular, not free text); writes/curation; ClinVar/CAR enrichment
beyond IDs already present; live clinical decision support (explicitly disclaimed).

---

## 2. Data acquisition architecture

**Hybrid: a self-contained SQLite snapshot for all four domains + a thin live HTTP layer
for single-record drill-down.** This combines genereviews' ETL+versioning discipline with
gnomad's live-proxy resilience, but uses **SQLite (not Postgres+pgvector)** — justified
because ClinGen data is small, structured, and needs no semantic search; SQLite ships inside
the wheel/release for zero-dependency, offline, token-efficient queries.

### 2.1 Sources per domain (decided)

| Domain | Snapshot source (ETL) | Live drill-down |
|---|---|---|
| Validity | `GET search.clinicalgenome.org/api/validity` (full JSON, 3,615 rows, ~1.8 MB; params ignored → one pull) | — |
| Dosage | FTP TSVs: `ftp.clinicalgenome.org/ClinGen_gene_curation_list_GRCh38.tsv` + `_GRCh37` + `ClinGen_region_curation_list_GRCh3{7,8}.tsv` (23 cols: HI/TS score+desc, 6 PMIDs each, disease MONDO, eval date) | — |
| Actionability | `GET actionability.clinicalgenome.org/ac/api/summ/brief` (index: gene→disease, status, release) + `/ac/{Adult,Pediatric}/api/summ` (outcomes/interventions/assertions) | `/ac/{Adult,Pediatric}/api/sepio/doc/{docId}` (full SEPIO assertion) |
| ERepo | `GET erepo.clinicalgenome.org/evrepo/api/summary/classifications/download` (TSV, ~12,683 rows, 20 cols incl. ACMG Met/Not-Met, PubMed, CSpec, dates, Retracted, Uuid) | `/evrepo/api/classifications?caid=|hgvs=|gene=&format=json` and `/evrepo/api/interpretation/{uuid}?format=json` (full evidence-code SEPIO) |

### 2.2 Freshness / update detection (decided)

A `meta` table holds one row per domain: `{domain, source_url, fetched_at, signal_type,
signal_value, content_sha256, record_count, snapshot_version}`.

- **Dosage** — cheapest: FTP files **support conditional GET** (verified `If-None-Match`/
  `If-Modified-Since` → HTTP 304). Store `ETag`+`Last-Modified` per file; a `HEAD`/conditional
  `GET` detects change without download.
- **ERepo** — pre-check `GET /evrepo/api/summary/news/` top `relatedVersion` (e.g. `2.5.6`);
  if bumped, re-pull TSV. Confirm via `record_count` + `max(Published Date)` + `sha256` of
  `(Uuid, Approval Date, Retracted)` tuples.
- **Validity** — `signal = max(row.date ISO)`; + `record_count` + `sha256` of canonical rows
  (the JSON API has no daily banner, unlike the CSV, so the hash is stable).
- **Actionability** — `signal = max(metadata.lastUpdated)`; + `sha256` of `(docId, release,
  lastUpdated)` tuples.

ETL CLI: `clingen-link refresh` (fetch → compute signal → rewrite domain tables atomically
only if changed) and `clingen-link refresh --check` (dry-run staleness report, exit non-zero
if stale). Provenance surfaced in `get_server_capabilities`, every tool's `_meta.data_version`,
and a `clingen://freshness` resource. A weekly GitHub Action runs `refresh` and commits/
publishes the snapshot bundle (`.sqlite` + `.sha256`) as a Release asset (genereviews pattern,
simplified for SQLite).

### 2.3 Distribution

Build the snapshot via ETL; commit a compressed `clingen.sqlite.zst` (~few MB) and/or publish
as a GitHub Release asset. At runtime the store opens the bundled DB read-only (WAL, immutable
pragma). If absent, the server starts in a degraded mode that tells the caller to run
`clingen-link refresh` (surfaced via diagnostics). Snapshot building is **never** done at
request time.

---

## 3. SQLite snapshot schema

Read-only at serve time; built by ETL. FTS5 virtual tables for text search.

- `gene` — canonical index: `symbol PK, hgnc_id, name, has_validity, has_dosage,
  has_actionability, erepo_variant_count`. Drives `search_genes` + `get_gene_summary`.
- `gene_alias` — `alias, symbol` (built from symbol/prev-symbol fields across feeds; case-insensitive resolution).
- `validity` — `symbol, hgnc_id, disease_name, mondo, moi, sop, classification, expert_panel,
  affiliate_id, perm_id, report_id, released, classified_date`.
- `dosage` — `record_type(gene|region), symbol, hgnc_id, isca_id, cytoband, grch37, grch38,
  haplo_score, haplo_description, haplo_disease, haplo_mondo, haplo_pmids(json),
  triplo_score, triplo_description, triplo_disease, triplo_mondo, triplo_pmids(json),
  date_last_evaluated`.
- `actionability` — `doc_id, curation_type, disease, modes_of_inheritance(json), last_updated,
  last_author, adult_status, adult_release, adult_sepio_iri, pediatric_status,
  pediatric_release, pediatric_sepio_iri, genes(json), assertions(json)`.
- `erepo` — `caid, clinvar_variation_id, variation, hgvs(json), gene, disease, mondo, moi,
  assertion, evidence_codes_met(json), evidence_codes_not_met(json), summary, pubmed(json),
  expert_panel, guideline_cspec, approval_date, published_date, retracted, uuid, repo_link`.
- `expert_panel` — `affiliate_id(curie), label, total_curations`.
- `meta` — per-domain freshness row (§2.2).
- FTS5: `validity_fts` (disease_name, gene), `dosage_fts` (symbol, isca_id, disease),
  `actionability_fts` (disease, gene), `erepo_fts` (gene, disease, hgvs), `expert_panel_fts`.

---

## 4. MCP tool surface (v1)

Hand-authored FastMCP v3 facade. Every tool: `Annotated[..., Field(description, pattern,
examples, bounds)]` params, `Literal` enums, `response_mode` (`minimal|compact|standard|full`,
default `compact`), `output_schema=relax_output_schema(...)`, `READ_ONLY_OPEN_WORLD`, returns a
`dict` via `run_mcp_tool(...)` (never raises). Names match `^[a-zA-Z0-9_-]{1,64}$`.

**Discovery**
1. `get_server_capabilities` — inventory, per-domain data version/freshness, payload modes,
   citation contract, error taxonomy, token-cost hints, `capabilities_version` sha256.

**Gene hub (primary entrypoint)**
2. `search_genes(query, …)` — resolve symbol/HGNC/alias → canonical gene + per-domain
   availability & counts; `_meta.next_commands` → `get_gene_summary`.
3. `get_gene_summary(gene, response_mode)` — flagship one-call cross-domain overview: validity
   classifications by disease, dosage haplo/triplo scores, actionability adult/pediatric,
   ERepo variant counts. Token-efficient.

**Gene-Disease Validity** (snapshot)
4. `get_gene_validity(gene, classification?, moi?)`
5. `search_validity(disease?|mondo?, expert_panel?, classification?, moi?, gene?, page, size)`

**Gene Dosage** (snapshot)
6. `get_gene_dosage(gene)` — haplo/triplo score + interpretation, coords (both builds),
   disease/MONDO, PMIDs.
7. `search_dosage(query?|region?|cytoband?, haplo_score?, triplo_score?, record_type?, page, size)`

**Clinical Actionability** (snapshot + live SEPIO)
8. `get_gene_actionability(gene, context?, include_detail?)` — adult/pediatric assertions,
   disease, status, release, SEPIO links; `include_detail=true` fetches live SEPIO.
9. `search_actionability(disease?|gene?, context?, assertion?, page, size)`

**Variant Pathogenicity / ERepo** (snapshot + live)
10. `get_variant_interpretations(gene|condition|expert_panel, classification?, page, size)` —
    list (CAID, canonical HGVS, MONDO, classification, VCEP, dates, permalink).
11. `get_variant_interpretation(caid|hgvs|clinvar_variation_id, refresh?)` — full ACMG criteria
    (evidence codes Met/Not Met), outcome+LOINC, guideline/CSpec, PubMed evidence, warnings,
    permalink. `refresh=true` bypasses snapshot for live SEPIO JSON.

**Reference**
12. `list_expert_panels(query?)` — GCEP/VCEP affiliates + curation counts.

**Diagnostics**
13. `get_clingen_diagnostics()` — recent-errors ring buffer, snapshot freshness, upstream
    reachability (house pattern).

(~13 tools; comparable to gnomad-link/sysndd. `search_*` may consolidate if surface feels heavy.)

### Response envelope (house canonical — copy from gnomad-link)
- Success: tool dict + `headline` + `success:true` + `_meta{ data_version, fetched_at,
  record_count, truncated?, next_commands:[{tool,arguments}], recommended_citation,
  unsafe_for_clinical_use:true }`.
- Error: `success:false` + `error_code` ∈ {`not_found`,`invalid_input`,`rate_limited`,
  `validation_failed`,`upstream_unavailable`,`snapshot_unavailable`,`output_validation_failed`,
  `internal_error`} + `retryable` + `recovery_action` ∈ {`retry_backoff`,`reformulate_input`,
  `switch_tool`} + `fallback_tool`/`fallback_args`/`recovery` + `_meta.next_commands` (diagnostics
  appended last).
- **Citation contract:** every record carries `recommended_citation` (verbatim) + stable
  permalink: Validity `CGGV` perm_id; Dosage HGNC/ISCA report page; Actionability `AC####` +
  SEPIO IRI; ERepo `CAR:CAxxxxx` + interpretation `@id`. Plus the framework citation (Strande
  et al. 2017, PMID 28552198) and license (CC BY 4.0, © ClinGen) in capabilities.

### Resources & instructions
- `clingen://capabilities`, `clingen://usage`, `clingen://reference` (error taxonomy +
  truncation contract + field glossary), `clingen://freshness` (per-domain version/date),
  `clingen://research-use`, `clingen://citations`.
- `FastMCP(instructions=…)` describing the canonical workflow (`search_genes → get_gene_summary
  → drill into a domain → get_variant_interpretation`), the `next_commands` chaining contract,
  and ending with the research-use notice.

---

## 5. Package layout (flat, `clingen_link/`)

```
clingen-link/
  clingen_link/
    __init__.py            # __version__
    config.py              # pydantic-settings Settings (env_prefix="CLINGEN_LINK_") + ServerConfig
    server_manager.py      # UnifiedServerManager: start_{unified,http,stdio}_server
    logging_config.py      # transport-aware; stdio→stderr, banners suppressed
    exceptions.py
    cli.py                 # typer CLI: serve, refresh, refresh --check, health, config
    api/                   # live HTTP layer (httpx.AsyncClient)
      base_client.py       # semaphore concurrency + jittered retry + queue-wait→rate_limited + typed faults
      clingen_client.py    # ERepo/actionability SEPIO live endpoints
    etl/                   # snapshot builder (offline; not in request path)
      __init__.py
      fetch.py             # source fetchers (validity JSON, dosage FTP TSV, actionability, erepo TSV) + freshness signals
      parse.py             # TSV/JSON parsers → normalized rows
      build.py             # create schema, write tables (staging→atomic rename), meta rows
      freshness.py         # signal computation + change detection
    store/                 # read-only SQLite query layer
      db.py                # connection (read-only, WAL), gene resolution/alias
      queries.py           # per-domain query functions
    services/              # business logic; async-lru caching; merges store + live
      gene_service.py, validity_service.py, dosage_service.py,
      actionability_service.py, erepo_service.py
    models/                # Pydantic request/response models per domain
    data/
      clingen.sqlite.zst   # shipped snapshot bundle (+ .sha256)
    mcp/
      facade.py            # create_clingen_mcp(): FastMCP(...) + register_*
      service_adapters.py  # lazy service-factory singletons (test-injectable)
      errors.py            # canonical envelope + run_mcp_tool() (copy from gnomad/pubtator)
      next_commands.py     # cmd() + for_gene()/for_disease()/for_variant()
      annotations.py       # READ_ONLY_OPEN_WORLD (copy verbatim)
      schema_relax.py, output_validation.py  # (copy verbatim)
      resources.py         # capabilities/usage/reference/freshness/citations + RESEARCH_USE_NOTICE
      shaping.py           # compact/full/minimal shapers
      patterns.py          # GENE/HGNC/CAID/HGVS regex
      tools/
        __init__.py        # register_clingen_tools()
        metadata.py        # get_server_capabilities + clingen://* resources
        genes.py           # search_genes, get_gene_summary
        validity.py, dosage.py, actionability.py, erepo.py
        reference.py       # list_expert_panels
        diagnostics.py     # get_clingen_diagnostics
  server.py                # argparse --transport unified|http|stdio
  mcp_server.py            # thin stdio entry
  pyproject.toml, uv.lock, Makefile, AGENTS.md, CLAUDE.md, README.md, LICENSE (MIT)
  .pre-commit-config.yaml, .loc-allowlist, .env.example, .python-version, .gitignore
  scripts/check_file_size.py
  docker/                  # Dockerfile + compose overlays + README
  .github/workflows/       # ci.yml, docker.yml, release.yml, security.yml, data-refresh.yml + dependabot.yml
  tests/                   # unit/ integration/ + conftest.py (respx mocks, fixture snapshot)
```

Tooling (copy house config): Python ≥3.12, uv, hatchling, FastMCP v3 (`fastmcp>=3.2,<4`,
`mcp[cli]>=1.27`), ruff (line 100, select `E,W,F,I,N,UP,B,C4,S,T20,SIM,RUF`), **mypy
`strict=true`**, pytest+pytest-asyncio(`auto`)+**respx**, coverage `fail_under=80`,
**600-LOC/module cap**, `make ci-local` gate, pre-commit. Deps: `httpx`, `pydantic>=2.11`,
`pydantic-settings`, `structlog`, `orjson`, `rich`, `typer`, `fastapi`, `uvicorn[standard]`,
`gunicorn`, `async-lru`, `zstandard` (snapshot), stdlib `sqlite3`. ETL extra: none beyond stdlib
csv/httpx.

---

## 6. Testing strategy

- **Unit** (default, `-n auto`): parsers (fixture TSV/JSON in `tests/fixtures/`), freshness
  signal computation, store queries (against a tiny fixture `.sqlite` built in a session
  fixture), service logic, response shaping, envelope/error classification, each MCP tool via
  `mcp.call_tool(...)` with injected fake services + a fixture snapshot.
- **respx** mocks all httpx upstream (live ERepo/actionability SEPIO + ETL fetchers).
- **Integration** (`@pytest.mark.integration`, off by default): hit live ClinGen endpoints to
  detect schema drift; assert known invariants (e.g. validity total ≥ 3000, ERepo `news`
  parseable).
- **MCP contract:** capabilities lists every registered tool; every tool returns the envelope;
  `next_commands` reference real tools; output validates against relaxed schema.
- `make ci-local` (format-check, lint, lint-loc, typecheck, test-fast) is the completion gate.

---

## 7. Build phases (for the plan)

1. **Scaffold** — package skeleton, pyproject/uv, Makefile, configs, copy house infra
   (errors/next_commands/annotations/schema_relax/output_validation/logging/server_manager),
   transports, `get_server_capabilities` stub, CI. Green `make ci-local` with a hello tool.
2. **ETL + snapshot** — fetchers, parsers, freshness, `build.py`, `clingen-link refresh`,
   produce real `clingen.sqlite`; unit tests on fixtures.
3. **Store + services** — read-only query layer, gene resolution/alias, per-domain services
   with caching, live drill-down client.
4. **MCP tools** — implement all tools + resources + shaping + instructions + citation contract.
5. **Polish** — diagnostics, docs (README/AGENTS/CLAUDE), Docker, data-refresh workflow,
   coverage ≥80, snapshot bundle + integration drift tests.

---

## 8. Key risks & mitigations
- **Upstream schema drift / no OpenAPI** → integration drift tests; parsers tolerant of
  added columns; capabilities expose snapshot version so consumers see staleness.
- **Real-time CSV banners create false "updated"** → use JSON API for validity (no banner) and
  per-record-date/tuple hashes elsewhere; exclude banners from hashes.
- **ERepo live latency** → snapshot covers search; live only for single-variant detail with
  short-TTL cache keyed to `news` version.
- **600-LOC cap** → split tools/services per domain from the start.
- **Clinical-safety** → `unsafe_for_clinical_use:true` on every envelope + research-use notice
  in instructions/resources; CC BY 4.0 attribution surfaced.
