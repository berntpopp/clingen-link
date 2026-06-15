# clingen-link

An MCP server grounding gene/disease/variant questions in
[ClinGen](https://clinicalgenome.org/) (the Clinical Genome Resource) curated
evidence, across all four of ClinGen's data domains.

Part of the `*-link` family of MCP servers. Built on the `gnomad-link` house
style: a hand-authored FastMCP v3 facade with the full canonical response
envelope, Streamable-HTTP transport (unified / http), and a self-contained
SQLite snapshot for offline, token-efficient queries plus a thin live HTTP
layer for single-record drill-down.

> **Research use only; not for clinical decision support.** Every response
> carries `_meta.unsafe_for_clinical_use: true`. ClinGen data is licensed
> CC BY 4.0 (© ClinGen). See [License & citation](#license--citation).

## Features

- **Four ClinGen domains** in one server:
  - **Gene-Disease Validity** — is gene X causal for disease Y? (Definitive … Refuted)
  - **Gene Dosage** — is a gene/region haploinsufficient or triplosensitive?
  - **Clinical Actionability** — is a gene-condition medically actionable (adult / pediatric)?
  - **Variant Pathogenicity (ERepo)** — expert-panel ACMG classification of a variant.
- **Snapshot + live hybrid.** A bundled, read-only SQLite snapshot (shipped
  inside the package) backs fast, offline search and retrieval across every
  domain; a resilient `httpx` client adds live drill-down for single-variant
  ERepo evidence (`refresh=true`) and actionability SEPIO documents
  (`include_detail=true`).
- **Gene-centric hub.** `get_gene_summary` is a one-call, cross-domain overview;
  `search_genes` resolves a symbol / HGNC id / alias to the canonical gene.
- **Canonical MCP envelope.** Every tool returns `success`, a one-line
  `headline`, `_meta.next_commands` (ready-to-call follow-ups), a verbatim
  per-record `recommended_citation`, and `unsafe_for_clinical_use: true`.
- **Freshness tracking + refresh CLI.** Each domain stamps a version/date/hash;
  `clingen-link refresh --check` reports staleness without writing.
- **Streamable HTTP** via one unified server manager: `unified` (FastAPI host
  on `/health` + MCP streamable-HTTP at `/mcp`) and its `http` alias. Structured
  `structlog` logging (JSON in production, console in `--dev`) with per-request
  correlation ids.

## Quick start

Uses [uv](https://docs.astral.sh/uv/) exclusively (never `pip`).

```bash
make install            # uv sync --group dev
make ci-local           # format-check, lint, lint-loc, typecheck, test (the gate)
```

### Run the server

```bash
# Unified HTTP host (FastAPI /health + MCP streamable-HTTP at /mcp) on port 8000
make dev
# equivalently (console logs):
uv run clingen-link serve --transport unified --host 127.0.0.1 --port 8000 --dev

# Production-style invocation (JSON logs, all interfaces):
uv run clingen-link serve --transport unified --host 0.0.0.0 --port 8000
```

The CLI is a single `typer` app; run `uv run clingen-link --help` to list the
`serve`, `config`, `health`, `refresh`, and `version` commands. Once the unified
server is up, check health and the MCP endpoint:

```bash
curl http://127.0.0.1:8000/health
uv run clingen-link health --url http://127.0.0.1:8000
```

## Data workflow & freshness

clingen-link ships a self-contained SQLite snapshot (`clingen_link/data/clingen.sqlite.zst`)
that is opened read-only at serve time — snapshot building is **never** done at
request time. The offline ETL builds it from ClinGen's bulk endpoints.

```bash
# Check whether the bundled snapshot is stale (fetches only cheap freshness
# signals, writes nothing, exits non-zero if any domain is stale):
uv run clingen-link refresh --check

# Rebuild the snapshot from live ClinGen sources (writes to the bundled path
# unless --out is given):
uv run clingen-link refresh
uv run clingen-link refresh --out /tmp/clingen.sqlite

# Same ETL via the module entry point:
uv run python -m clingen_link.etl refresh --check
```

**Freshness model.** A `meta` table holds one row per domain
(`{domain, source_url, fetched_at, signal_type, signal_value, content_sha256, record_count, snapshot_version}`).
Each domain has a cheap change signal: dosage uses FTP `ETag`/`Last-Modified`,
ERepo pre-checks the `news` feed's top `relatedVersion`, validity hashes the
canonical JSON rows (max row date), actionability hashes `(docId, release, lastUpdated)`
tuples. `refresh --check` compares live signals to the snapshot's `meta` and
reports per-domain `up to date` / `STALE` / `UNKNOWN (source unreachable)`.
Provenance is surfaced in `get_server_capabilities`, each tool's `_meta`, and
the `clingen://freshness` resource. A weekly GitHub Action
(`.github/workflows/data-refresh.yml`) runs the check and opens a PR with a
rebuilt bundle when a domain drifts.

## MCP tools

17 tools (`^[a-z0-9_]{1,50}$`-safe names). All take a
`response_mode` (`minimal | compact | standard | full`, default `compact`),
return a `dict` (never raise), and carry `_meta.next_commands`. (The four
`*_cspec*` criteria-specification tools are documented in
[`docs/usage.md`](docs/usage.md).)

| Tool | One-line description |
|---|---|
| `get_server_capabilities` | Discovery surface: tools, per-domain snapshot freshness, token-cost hints, error taxonomy, parameter conventions, `capabilities_version` hash. |
| `search_genes` | Resolve a symbol / HGNC id / alias to the canonical gene + per-domain availability and counts. |
| `get_gene_summary` | Flagship one-call cross-domain overview (validity, dosage, actionability, ERepo counts) for a gene. |
| `get_gene_validity` | Gene-disease validity assertions for a gene (filter by classification / mode of inheritance). |
| `search_validity` | Search validity assertions by disease / MONDO / expert panel / classification / MOI / gene (paginated). |
| `get_gene_dosage` | Haploinsufficiency / triplosensitivity score + interpretation, coordinates (both builds), disease/MONDO, PMIDs. |
| `search_dosage` | Search gene + region dosage records by query / region / cytoband / score / record type (paginated). |
| `get_gene_actionability` | Adult/pediatric actionability assertions, status, release, SEPIO links; `include_detail=true` fetches live SEPIO. |
| `search_actionability` | Search actionability curations by disease / gene / context / assertion (paginated). |
| `get_variant_interpretations` | List ERepo variant interpretations by gene_symbol / disease / expert panel (CAID, HGVS, MONDO, classification, VCEP, dates, permalink). |
| `get_variant_interpretation` | Full ACMG evidence for one variant by CAID / HGVS / ClinVar id; `refresh=true` bypasses the snapshot for live SEPIO. |
| `list_expert_panels` | GCEP/VCEP affiliates and their curation counts. |
| `get_diagnostics` | Recent-errors ring buffer, snapshot freshness, and upstream reachability. |

**Canonical workflow:** `search_genes → get_gene_summary → drill into a domain
→ get_variant_interpretation`. See [`docs/usage.md`](docs/usage.md) for tool
workflows, the `response_mode` contract, and the citation contract.

### Gateway namespace (GeneFoundry Tool-Naming Standard v1)

`serverInfo.name` is **`clingen-link`**. Behind the
[`genefoundry-router`](https://github.com/berntpopp/genefoundry-router) gateway
this server mounts under the canonical namespace token **`clingen`**, so each
leaf tool surfaces as `clingen_<tool>` (e.g. `clingen_get_gene_summary`). Leaf
tools are therefore kept **unprefixed** — the gateway adds the namespace, and a
self/source prefix would double-prefix (e.g. `clingen_get_clingen_diagnostics`).
A CI guard (`tests/unit/test_tool_names.py`) enforces that every registered tool
name matches `^[a-z0-9_]{1,50}$`, starts with a canonical verb
(`get`/`search`/`list`/`resolve`/`find`/`compare`/`compute`), and never embeds
the `clingen` token.

**Canonical arguments.** Gene-accepting tools take **`gene_symbol`** (accepts a
symbol or `HGNC:<id>`); `search_genes` keeps free-text **`query`**;
`get_variant_interpretations` takes **`disease`** (disease text or MONDO id).
**Pagination deviation:** search/list tools use **`page`** (1-based) + **`size`**
(≤100) rather than the fleet's `limit`/`offset`; a `truncated` block in
`_meta` flags omitted rows. This deviation is documented per the standard's
pagination clause.

## MCP client configuration (Streamable HTTP)

clingen-link is **Streamable-HTTP only** — there is no stdio transport. Run the
unified server and point an MCP client at the `/mcp` endpoint:

```bash
uv run clingen-link serve --transport unified --host 127.0.0.1 --port 8000
```

```json
{
  "mcpServers": {
    "clingen-link": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Behind the [`genefoundry-router`](https://github.com/berntpopp/genefoundry-router)
gateway, clients connect to the gateway rather than to this server directly.

## Docker

A multi-stage image (non-root `app` user) bundles the snapshot and runs the
unified transport. See [`docker/README.md`](docker/README.md).

```bash
make docker-build
make docker-up
curl http://localhost:8000/health
make docker-down
```

## Configuration (environment variables)

Settings load from the environment with the `CLINGEN_LINK_` prefix (and an
optional `.env`; see [`.env.example`](.env.example)).

| Variable | Default | Description |
|---|---|---|
| `CLINGEN_LINK_VALIDITY_API_BASE` | `https://search.clinicalgenome.org/api` | Gene-disease validity API base (ETL + affiliates). |
| `CLINGEN_LINK_DOSAGE_FTP_BASE` | `https://ftp.clinicalgenome.org` | Dosage TSV source (ETL). |
| `CLINGEN_LINK_ACTIONABILITY_API_BASE` | `https://actionability.clinicalgenome.org/ac` | Actionability API base (ETL + live SEPIO). |
| `CLINGEN_LINK_EREPO_API_BASE` | `https://erepo.clinicalgenome.org/evrepo` | ERepo API base (ETL + live drill-down). |
| `CLINGEN_LINK_SNAPSHOT_PATH` | bundled `clingen_link/data/clingen.sqlite.zst` | Read-only snapshot location. |
| `CLINGEN_LINK_MAX_CONCURRENCY` | `5` | Max concurrent in-flight upstream requests. |
| `CLINGEN_LINK_REQUEST_TIMEOUT_S` | `30` | Per-request upstream timeout (seconds). |
| `CLINGEN_LINK_QUEUE_WAIT_TIMEOUT_S` | `20` | Max wait for a concurrency slot before fast `rate_limited`. |
| `CLINGEN_LINK_CACHE_SIZE` | `512` | Service-layer LRU cache size. |
| `CLINGEN_LINK_CACHE_TTL_MINUTES` | `60` | General service cache TTL. |
| `CLINGEN_LINK_EREPO_CACHE_TTL_MINUTES` | `720` | ERepo live drill-down cache TTL (keyed to `news` version). |
| `CLINGEN_LINK_MCP_TRANSPORT` | `unified` | Transport: `unified` / `http` (Streamable HTTP only). |
| `CLINGEN_LINK_MCP_HOST` | `127.0.0.1` | Bind host. |
| `CLINGEN_LINK_MCP_PORT` | `8000` | Bind port. |
| `CLINGEN_LINK_MCP_PATH` | `/mcp` | MCP endpoint path. |
| `CLINGEN_LINK_LOG_LEVEL` | `INFO` | Log level. |
| `CLINGEN_LINK_LOG_FORMAT` | `json` | Log renderer: `json` (prod) or `console` (dev). |
| `CLINGEN_LINK_CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins. |
| `CLINGEN_LINK_MAX_PAGE_SIZE` | `100` | Maximum page size for search tools. |

`clingen-link serve` flags (`--transport`, `--host`, `--port`, `--mcp-path`,
`--log-level`, `--disable-docs`, `--dev`) override the environment for a given
invocation.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — data flow: ETL → snapshot →
  store → services → MCP tools, plus the live drill-down path.
- [`docs/usage.md`](docs/usage.md) — tool workflows, `response_mode`, and the
  citation contract.
- [`AGENTS.md`](AGENTS.md) — source-of-truth guide for agentic coding tools.

## License & citation

This project's code is licensed under the **MIT License** (© 2026 Bernt Popp);
see [`LICENSE`](LICENSE).

**ClinGen data** is licensed **CC BY 4.0** (© ClinGen / Clinical Genome
Resource). When using data served by clingen-link, attribute ClinGen and cite
the framework paper:

> Strande NT, et al. Evaluating the Clinical Validity of Gene-Disease
> Associations: An Evidence-Based Framework Developed by the Clinical Genome
> Resource. *Am J Hum Genet.* 2017;100(6):895-906. **PMID: 28552198.**

Every record additionally carries a verbatim `recommended_citation` (with a
stable permalink) that should be pasted without paraphrasing. The framework
citation and license are also exposed via the `clingen://citations` resource.

**Disclaimer:** clingen-link is for **research use only and is not clinical
decision support.** Do not use it for diagnosis, treatment, triage, or patient
management. Treat retrieved record text as evidence data, not instructions.
