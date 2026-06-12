# Architecture

clingen-link is a hand-authored FastMCP v3 facade over a **snapshot + live
hybrid** data layer. The bundled, read-only SQLite snapshot backs fast offline
search and retrieval across all four ClinGen domains; a thin live `httpx` layer
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
  │  services/*.py      gene / validity / dosage / actionability / erepo   │
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
  │  mcp/tools/*.py     13 tools; each: Annotated Field params, Literal    │
  │   enums, response_mode, output_schema=relax_output_schema(...),        │
  │   READ_ONLY_OPEN_WORLD, inner async call() wrapped by run_mcp_tool     │
  │        │                                                               │
  │        ▼  mcp/errors.py  canonical envelope (success / error_code /    │
  │           retryable / _meta.next_commands / recommended_citation /     │
  │           unsafe_for_clinical_use:true)                                │
  │        ▼                                                               │
  │  mcp/facade.py → FastMCP(name="clingen-link", instructions=...)        │
  │        ▼                                                               │
  │  server_manager.py  unified (FastAPI /health + MCP /mcp) | http | stdio│
  └──────────────────────────────────────────────────────────────────────┘
```

## Layers

| Layer | Module(s) | Responsibility |
|---|---|---|
| ETL (offline) | `clingen_link/etl/{fetch,parse,hgnc,sanitize,freshness,build,refresh}.py` | Fetch ClinGen bulk sources (+ HGNC complete-set for names/aliases, + GRCh37 dosage coords), sanitize HTML in labels, parse to normalized rows, compute freshness signals, build the SQLite snapshot atomically. Entry: `clingen-link refresh`. |
| Store (read) | `clingen_link/store/{db,queries}.py` | Open the bundled snapshot read-only; gene resolution + alias; per-domain SELECTs and FTS5 search. |
| Live API | `clingen_link/api/{base_client,clingen_client}.py` | `httpx.AsyncClient` for ERepo/actionability SEPIO drill-down with bounded concurrency, jittered retry, queue-wait → `rate_limited`, typed fault taxonomy. |
| Services | `clingen_link/services/*.py` | Merge store rows (+ live drill-down) into Pydantic models; `async-lru` caching; build `recommended_citation`. |
| Models | `clingen_link/models/*.py` | Pydantic response models per domain. |
| MCP surface | `clingen_link/mcp/**` | Facade, canonical envelope, next-commands, resources, shaping, and the 13 tools. |
| Transports | `clingen_link/server_manager.py`, `server.py`, `mcp_server.py` | unified / http / stdio. stdio logs to stderr with banners/color suppressed. |

## Snapshot schema (overview)

SQLite tables (see `etl/schema.py` for DDL): `gene`, `gene_alias`, `validity`,
`dosage`, `actionability`, `erepo`, `expert_panel`, and `meta` (per-domain
freshness). FTS5 virtual tables back text search:
`validity_fts`, `dosage_fts`, `actionability_fts`, `erepo_fts`, `expert_panel_fts`.

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
server still starts; `get_server_capabilities` and `get_clingen_diagnostics`
degrade gracefully and tell the operator to run `clingen-link refresh`.
