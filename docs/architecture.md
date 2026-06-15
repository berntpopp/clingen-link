# Architecture

clingen-link is a hand-authored FastMCP v3 facade over a **snapshot + live
hybrid** data layer. The bundled, read-only SQLite snapshot backs fast offline
search and retrieval across all five ClinGen domains; a thin live `httpx` layer
adds single-record drill-down. Snapshot building is an **offline** concern and
is never done in the request path.

## Data flow

```
                          OFFLINE (clingen-link refresh)
  ┌──────────────────────────────────────────────────────────────────────┐
  │  ClinGen sources                                                       │
  │   • validity   GET search.clinicalgenome.org/api/validity (JSON)       │
  │   • dosage     ftp.clinicalgenome.org/ClinGen_*_curation_list_*.tsv    │
  │                (GRCh38 + GRCh37 gene & region; GRCh37 backfills coords) │
  │   • actionability  actionability.clinicalgenome.org/ac/api/summ/brief  │
  │   • erepo      erepo.clinicalgenome.org/evrepo/api/.../download (TSV)   │
  │   • cspec      cspec.genome.network — paged catalog + per-spec JSON-LD  │
  │                + rendered doc-page HTML scrape (two sources; see below) │
  │   • hgnc       HGNC complete-set TSV (gene full name + alias/prev sym)  │
  │        │                                                               │
  │        ▼  etl/fetch.py        (httpx, sync; tagged SourceFetchError)   │
  │   raw bytes / rows / etags                                            │
  │        │                                                               │
  │        ▼  etl/parse.py        (TSV/JSON → normalized rows)             │
  │   normalized domain rows                                              │
  │        │                                                               │
  │        ├─▶ etl/freshness.py   (per-domain signal: version/date/sha256) │
  │        │                                                               │
  │        ▼  etl/build.py        (create schema → write tables + FTS5 →   │
  │                                meta rows → PRAGMA optimize → os.replace)│
  │   clingen.sqlite  ──compress──▶  data/clingen.sqlite.zst  (+ .sha256)  │
  └──────────────────────────────────────────────────────────────────────┘
                                   │  shipped in the package / image
                                   ▼
                          SERVE TIME (read-only)
  ┌──────────────────────────────────────────────────────────────────────┐
  │  store/db.py        opens the .zst → temp .sqlite, read-only           │
  │   • resolve_gene(symbol | HGNC | alias) → canonical symbol             │
  │   • meta() → per-domain freshness                                     │
  │  store/queries.py   per-domain SELECTs + FTS5 search                   │
  │        │                                                               │
  │        ▼                                                               │
  │  services/*.py      gene / validity / dosage / actionability / erepo / │
  │                     cspec                                              │
  │   • merge store rows into Pydantic models                             │
  │   • async-lru caching on hot reads                                    │
  │   • build per-record recommended_citation + permalink                 │
  │        │                         ▲                                     │
  │        │                         │  live drill-down (only when asked)  │
  │        │                  api/clingen_client.py  (httpx.AsyncClient)   │
  │        │                   • erepo_interpretation (refresh=true)       │
  │        │                   • actionability_sepio (include_detail=true) │
  │        │                   • semaphore + jittered retry + rate_limited │
  │        ▼                                                               │
  │  mcp/tools/*.py     17 tools; each: Annotated Field params, Literal    │
  │   enums, response_mode, output_schema=relax_output_schema(...),        │
  │   READ_ONLY_OPEN_WORLD, inner async call() wrapped by run_mcp_tool     │
  │        │                                                               │
  │        ▼  mcp/errors.py  canonical envelope (success / error_code /    │
  │           retryable / _meta.next_commands / recommended_citation /     │
  │           unsafe_for_clinical_use:true)                                │
  │        ▼                                                               │
  │  mcp/facade.py → FastMCP(name="clingen-link", instructions=...)        │
  │        ▼                                                               │
  │  server_manager.py  unified (FastAPI /health + MCP /mcp) | http alias  │
  └──────────────────────────────────────────────────────────────────────┘
```

## Layers

| Layer | Module(s) | Responsibility |
|---|---|---|
| ETL (offline) | `clingen_link/etl/{fetch,parse,hgnc,sanitize,freshness,build,refresh}.py` (+ `cspec_fetch,cspec_parse`) | Fetch ClinGen bulk sources (+ HGNC complete-set for names/aliases, + GRCh37 dosage coords), sanitize HTML in labels, parse to normalized rows, compute freshness signals, build the SQLite snapshot atomically. The cspec domain uses a two-source fetch (per-spec JSON-LD + doc-page HTML scrape; see below). Entry: `clingen-link refresh`. |
| Store (read) | `clingen_link/store/{db,queries}.py` | Open the bundled snapshot read-only; gene resolution + alias; per-domain SELECTs and FTS5 search. |
| Live API | `clingen_link/api/{base_client,clingen_client}.py` | `httpx.AsyncClient` for ERepo/actionability SEPIO drill-down with bounded concurrency, jittered retry, queue-wait → `rate_limited`, typed fault taxonomy. |
| Services | `clingen_link/services/*.py` | Merge store rows (+ live drill-down) into Pydantic models; `async-lru` caching; build `recommended_citation`. |
| Models | `clingen_link/models/*.py` | Pydantic response models per domain. |
| MCP surface | `clingen_link/mcp/**` | Facade, canonical envelope, next-commands, resources, shaping, and the 17 tools. |
| Transport / CLI | `clingen_link/server_manager.py`, `clingen_link/cli.py` (`typer`) | Streamable HTTP only: `unified` (FastAPI `/health` + mounted MCP `/mcp`) and its `http` alias. `structlog` logging (JSON prod / console dev) with `asgi-correlation-id`. |

## Snapshot schema (overview)

SQLite tables (see `etl/schema.py` for DDL): `gene`, `gene_alias`, `validity`,
`dosage`, `actionability`, `erepo`, `expert_panel`, the cspec family (`cspec`,
`cspec_rule_set`, `cspec_gene`, `cspec_criteria`, `cspec_strength`,
`cspec_file`), and `meta` (per-domain freshness). FTS5 virtual tables back text
search: `validity_fts`, `dosage_fts`, `actionability_fts`, `erepo_fts`,
`expert_panel_fts`, and `cspec_fts` (a mixed-entity index over specs, criteria,
and filenames, with each rowid resolved back to its source entity through the
`cspec_search_doc` row-map). `cspec_criteria` uses the numeric `criteria_id` as
its primary key rather than `(gn_id, code)`, because a code repeats across the
multiple rule sets of a multi-rule-set spec. That `criteria_id` is unique per
criterion, but the registry can reuse the same `criteria_id` across multiple rule
sets within a spec — so it is not globally unique across rule-set occurrences. The
build collapses those reuses to one `cspec_criteria` row and indexes each distinct
criterion in `cspec_fts` exactly once.

## CSpec two-source ETL

Unlike the other domains — each fed by a single bulk source — the cspec domain
(`cspec.genome.network`, the ClinGen Criteria Specification Registry) is built
from **two sources per spec**, both fetched offline by `clingen-link refresh`
and never on the request path:

- **Structured criteria** come from the per-spec JSON-LD
  (`/cspec/api/SequenceVariantInterpretation/id/<GN>`): rule sets, genes/diseases,
  ACMG/AMP criteria codes, and their strength levels + applicability.
- **Attachment links** are *not* in the JSON-LD; they are scraped from the
  rendered doc-page "Files & Images" panel (`/cspec/ui/svi/doc/<GN>`). Each file
  is attributed to a criterion by the ACMG/AMP code named in its **own authored
  `file-label` title** (e.g. "PM3 table", "ABCA4 PVS1 Flowchart") — *not* by
  document position, since every attachment sits in a trailing panel after all
  criteria. A file binds to a criterion only when its label names exactly one
  resolvable code; a title that names zero codes (spec-wide docs like
  "Specifications" / "Appendices"), two or more codes (a shared "PS3 and BS3
  flowchart"), or an ambiguous code stays **spec-level** (`criteria_id = NULL`)
  and surfaces in the spec's top-level `files` rather than under any one
  criterion. Each file's metadata (filename, content-type, size) comes from a
  **streaming GET that reads only the response headers** — the File endpoint
  rejects `HEAD` with HTTP 400.

The catalog of spec headers comes from the documented paged list endpoint. A
spec is included only when `cspecStatus == "Released"` and it carries at least
one criterion (plus the baseline GN001), so unpublished or empty specs never
enter the snapshot.

## Live drill-down

Almost everything is served from the offline snapshot. Only two paths hit live
ClinGen:

- `get_variant_interpretation(refresh=true)` → live ERepo SEPIO JSON (full
  evidence-code ACMG criteria), cached on a long TTL keyed to the ERepo `news`
  version (`CLINGEN_LINK_EREPO_CACHE_TTL_MINUTES`, default 720).
- `get_gene_actionability(include_detail=true)` → live actionability SEPIO
  assertion document.

The live client converts upstream faults into the typed taxonomy
(`ClingenApiError` → `DataNotFoundError` / `UpstreamInputError` /
`RateLimitedError`), which `mcp/errors.py:_classify()` maps to envelope
`error_code`s. Concurrency saturation becomes a fast, retryable `rate_limited`
error rather than a hang.

## Freshness & refresh

A `meta` row per domain records `{domain, source_url, fetched_at, signal_type,
signal_value, content_sha256, record_count, snapshot_version}`. `refresh --check`
fetches only the cheap signal for each domain and compares `content_sha256` to
the snapshot's `meta`, reporting `up to date` / `STALE` / `UNKNOWN (source
unreachable)`. Provenance surfaces in `get_server_capabilities`, every tool's
`_meta`, and the `clingen://freshness` resource. The weekly
`.github/workflows/data-refresh.yml` Action automates the check and opens a PR
with a rebuilt bundle when a domain drifts.

## Degraded mode

If the snapshot is missing or unreadable, the store raises
`SnapshotUnavailableError`, mapped to the `snapshot_unavailable` error code. The
server still starts; `get_server_capabilities` and `get_diagnostics`
degrade gracefully and tell the operator to run `clingen-link refresh`.
