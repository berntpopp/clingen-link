# Changelog

All notable changes to clingen-link are documented here.

## [2.0.4] - 2026-07-03

Fix a production crash loop (closes #26). `docker/docker-compose.npm.yml` set
`read_only: true` but relied on the base compose to supply the
`/tmp/clingen-link` tmpfs — an inheritance that never happens: the npm overlay
is deployed as a single, self-contained compose file (the GeneFoundry `-link`
fleet standard) under its own service key (`clingen_link`, distinct from the
base's `clingen-link`), so nothing from the base merges in. The result was a
read-only rootfs with no writable temp, and the snapshot `.zst` decompress at
startup (`store/db.py`) crash-looped with `No usable temporary directory`.
Make the npm overlay self-contained: declare the writable `/tmp/clingen-link`
tmpfs and `security_opt: no-new-privileges` directly, and correct the compose
hardening test to enforce the real invariant (the npm overlay MUST carry its
own tmpfs; the prod overlay — same service key, layered — inherits it).

## [2.0.3] - 2026-07-03

Single-source the package version. `clingen_link/__init__.py` now derives
`__version__` from installed distribution metadata
(`importlib.metadata.version("clingen-link")`) instead of a hardcoded literal,
so `pyproject.toml [project].version` is the sole source of truth (bump it and
metadata → `__version__` → `serverInfo` → `/health` all follow). Non-behavioral;
aligns clingen-link with the fleet versioning standard.

## [2.0.2] - 2026-07-03

Advertise the real package version in the MCP `initialize` response. The
`FastMCP(...)` constructor in `clingen_link/mcp/facade.py` had no `version=`
argument, so `serverInfo.version` defaulted to the FastMCP framework version
(`3.4.2`) instead of clingen-link's own version. Pass `version=__version__` so
hosts see `2.0.2`. `/health` was already correct. Non-breaking; the tool
surface and endpoints are unchanged.

## [2.0.1] - 2026-06-29

Adopt the **GeneFoundry Container & Deployment Hardening Standard v1** (closes #13):
pin the base image by digest (`python:3.14-slim@sha256:b877e50…`), add a CI
container scan + SBOM workflow, and never send CORS credentials with a wildcard
origin.

## [2.0.0] - 2026-06-15

Adopt the **GeneFoundry Logging & CLI Standard v1**. This is a **breaking**
release with no deprecation shims (pre-alpha, per the standard's Rule 7). The
MCP tool surface, services, and the `/health` / `/mcp` endpoints are unchanged,
so the `genefoundry-router` gateway is unaffected.

### Changed (BREAKING)

- **CLI: `argparse` → `typer`.** A single `typer` app (`clingen_link/cli.py`)
  replaces the old argparse parser. Commands: `serve`, `config`, `health`,
  `refresh`, `version`. There is no bare-serve — start the server with
  `clingen-link serve …`. `serve` options: `--transport {unified,http}`
  (default `unified`), `--host`, `--port`, `--mcp-path`, `--log-level`,
  `--disable-docs`, `--dev`.
- **Single console script.** `clingen-link = "clingen_link.cli:app"`. The old
  `clingen-link` (`server:main`), `clingen-link-mcp` (`mcp_server:main`), and
  `clingen-link-refresh` (`clingen_link.etl.refresh:main`) entry points are
  removed. The root `server.py` and `mcp_server.py` modules are deleted. The ETL
  is now reachable via `clingen-link refresh` or `python -m clingen_link.etl
  refresh`.
- **stdio removed.** The server is **Streamable HTTP only** (`unified` and its
  `http` alias). The `stdio` transport, its config Literals
  (`CLINGEN_LINK_STDIO_LOG_LEVEL` setting and the stdio transport value),
  `start_stdio_server`, and `STDIOTransportError` are gone. MCP clients connect
  to the `/mcp` HTTP endpoint (directly or via the gateway).
- **Logging: stdlib `logging` → `structlog`.** `clingen_link/logging_config.py`
  follows the fleet canon (`merge_contextvars → add_log_level → TimeStamper(iso)
  → StackInfoRenderer → format_exc_info → static fields service/version`), with a
  JSON renderer in production and a `ConsoleRenderer` in development selected by
  `CLINGEN_LINK_LOG_FORMAT` (default `json`; `--dev` forces `console`).
  Per-request correlation ids are bound via the `asgi-correlation-id`
  middleware and merged into every log event.

### Migration

- Replace `clingen-link --transport unified …` with `clingen-link serve
  --transport unified …`.
- Replace `clingen-link-mcp` (stdio) usage with the HTTP `/mcp` endpoint.
- Replace `clingen-link-refresh …` with `clingen-link refresh …`.
- Set `CLINGEN_LINK_LOG_FORMAT=console` for human-readable dev logs (default is
  JSON); `CLINGEN_LINK_STDIO_LOG_LEVEL` no longer exists.

## [1.0.0] - 2026-06-15

Adopt the **GeneFoundry Tool-Naming Standard v1** so the server composes cleanly
behind [`genefoundry-router`](https://github.com/berntpopp/genefoundry-router)
(tools surface as `clingen_<tool>` at the gateway). This is a **breaking**
release: tool and argument names change with no deprecation aliases, per the
standard's project decision (Rule 7).

### Changed (BREAKING)

- Renamed the discovery tool `get_clingen_diagnostics` → **`get_diagnostics`**.
  The embedded `clingen` source token was redundant under the gateway's
  `clingen_` namespace prefix (it produced `clingen_get_clingen_diagnostics`).
  The gateway-qualified name is now `clingen_get_diagnostics`. The payload,
  behaviour, and the service method are unchanged; update any direct callers of
  the tool name.
- Renamed the gene argument `gene` → **`gene_symbol`** (accepts a symbol or
  `HGNC:<id>`) on every gene-accepting tool: `get_gene_actionability`,
  `search_actionability`, `get_gene_dosage`, `get_gene_validity`,
  `search_validity`, `get_gene_summary`, `get_variant_interpretations`,
  `list_cspecs`, and `get_cspec`. `search_genes` keeps its free-text `query`
  argument.
- Renamed the ERepo list argument `condition` → **`disease`** on
  `get_variant_interpretations` (still accepts disease text or a MONDO id),
  aligning with `disease` used elsewhere in the server.
- `_meta.next_commands` now emit the canonical argument keys (`gene_symbol`,
  `disease`); any consumer that executed these follow-ups verbatim continues to
  work.

### Added

- Tool-name compliance test (`tests/unit/test_tool_names.py`): every registered
  tool must match `^[a-z0-9_]{1,50}$`, start with a canonical verb
  (`get|search|list|resolve|find|compare|compute`), and never embed the
  `clingen` source/self token (the gateway adds the namespace).
- README documents the canonical gateway **namespace token** `clingen` and the
  canonical argument names.

### Fixed

- Reconciled the package version to a single source: `pyproject.toml` and
  `clingen_link.__init__.__version__` are both `1.0.0`, and the FastAPI host
  (`server_manager.py`) now derives its version from `__version__` instead of a
  hard-coded literal.

### Notes

- **Pagination deviation (documented):** search/list tools keep `page` (1-based)
  + `size` (≤100) rather than the fleet's `limit`/`offset`. A `truncated` block
  in `_meta` flags omitted rows. This deviation is documented per the standard's
  pagination clause.
- `serverInfo.name` remains `clingen-link`.

## [0.1.0] - 2026-06-12

- Initial release: MCP server grounding gene/disease/variant questions in
  ClinGen's curated datasets (gene-disease validity, dosage sensitivity, clinical
  actionability, ERepo variant interpretations, and CSpec criteria
  specifications) with a bundled SQLite snapshot, freshness tracking, and a
  refresh CLI.
