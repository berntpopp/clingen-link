# Changelog

All notable changes to clingen-link are documented here.

## [3.0.5] - 2026-07-13

### Fixed

- **Signed release evidence now states the data contract this service actually declares.**
  The reusable release workflow hardcoded `--contract data-independent` and a fixed
  `data_requirements: {"mode":"none"}`, so every published manifest claimed the image binds
  to no data at all — while `container-release.json` declares `data-bound` against the
  immutable ClinGen bundle (`data-clingen-2026-07-13`,
  `sha256:e0204a40541e82fb86cf4725a2b5fa9edc5e0eec838ada9564fa8de973c51626`). Because the
  evidence assembler returns early for a data-independent contract, the strongest assertion
  in the chain — that the definition evidence binds to the exact pinned artifact — was
  silently skipped. Re-pinning the container-release standard to
  `86b11f7ed062ed84dfddcbd309e34da88f3dae5b` sources the contract and the exact data
  identity from `container-release.json`, so the manifest states the real binding and the
  assertion runs. The v3.0.4 image and its attestations are sound; only its evidence
  understated the binding, and regenerating that evidence requires this patch re-release.

## [3.0.4] - 2026-07-13

### Changed

- **The production image is now code-only.** The authoritative ClinGen snapshot is no
  longer baked into the image or committed to git. It ships as an immutable, attested
  GitHub data release (`data-clingen-2026-07-13`,
  `sha256:e0204a40541e82fb86cf4725a2b5fa9edc5e0eec838ada9564fa8de973c51626`) built by this
  repository's own ETL. The `clingen-data-init` sidecar verifies the reviewed bundle with
  no network access at all (`network_mode: none`) and atomically selects it into the
  `clingen-reference` volume; the server mounts that volume read-only.
- Adopt the GeneFoundry container-release standard, declaring `clingen-data-init` under
  `service.auxiliary` with its `init` role, `denied` egress, and exact writable and
  read-only mount targets. The sidecar is authorized by its role, not its name: the
  central gate validates every field the role permits.
- Move the reference mount and scratch space to the two paths the hardening policy
  approves: the snapshot volume is `/data` and `TMPDIR` is `/tmp`.
- Give every compose variable a default equal to the reviewed data identity, so
  `docker compose config` renders without a pre-populated environment. Only the attested
  image digest stays a required variable.

### Fixed

- A host started without its materialized snapshot no longer aborts at startup. It is
  **live but not ready**: `/health` answers `503 degraded`, MCP definitions stay servable,
  and every data-bearing tool call fails closed with a `snapshot_unavailable` envelope.
  Previously the process crashed, so a code-only image could never be started standalone.
- A snapshot whose identity does not match the deployment pins now raises the typed
  `SnapshotUnavailableError` rather than a bare `ValueError`, so it maps to the canonical
  fail-closed envelope instead of an internal error.
- The data-release publisher names its repository explicitly (it deliberately has no
  checkout, so `gh` had no remote to infer) and verifies published assets from the
  handoff directory.

## [3.0.2] - 2026-07-11

### Security

Security (defense in depth): close the FastMCP-core not-found reflection
residual (Response-Envelope Standard v1.1 §Error-message sanitation fast-follow).
FastMCP core (and the MCP SDK) reflect the caller's OWN requested tool name,
resource URI, or prompt name back to the caller and to logs BEFORE any
clingen-link middleware runs — echoing any control/zero-width/bidi/NUL code
points the caller embeds. This is a caller self-reflection surface (lower risk
than upstream injection), closed to keep caller input out of the shared log/OTel
sink and out of an agent's tool-result context. Research use only.

A new `mcp/notfound_guard.py` adds the layered fleet-standard guard, wired in the
facade:

- Layer 1 — `NotFoundGuard.on_call_tool` preflights the tool name via `get_tool`;
  an unknown name returns a FIXED, name-free `not_found` envelope (no `_meta.tool`
  echo) before core dispatch.
- Layer 2 — `NotFoundGuard.on_read_resource` re-raises a FIXED, URI-free
  `ResourceError` for any resource not-found/read failure (logs the exception
  class only, never the URI).
- Layer 3 — `install_protocol_error_handler` wraps the raw
  CallTool/ReadResource/GetPrompt request handlers as the outermost layer:
  replaces the unknown-tool *return* path (`Unknown tool: '<name>'`) with the
  fixed envelope and severs the unknown-**prompt** echo (`Unknown prompt:
  '<name>'`) — the only layer covering prompts.
- Layer 5 — `install_validation_log_filter` scrubs FastMCP-core / MCP-SDK
  validation-log records (root + `fastmcp` non-propagating Rich handlers +
  `fastmcp.server.*` / `mcp.server.lowlevel.server`) that would echo the
  caller-supplied name/URI at any level.

All fixed messages are built from constants only (sanitation strips code points
but preserves injection prose). No output-schema or envelope-shape change (PATCH).

Research use only; not clinical decision support.

## [3.0.1] - 2026-07-11

### Security

Security (defense in depth): caller-visible error messages are sanitized of
control/zero-width/bidi/NUL code points; the diagnostics detail, arg-validation
frame (now catches FastMCP's own ValidationError), and output-validation log no
longer expose exception detail/paths/argument names; upstream-influenced
parser/URL text no longer echoed. Research use only.

Detail — caller-visible error/message/diagnostics strings no longer carry
upstream-influenced or caller-influenced free text, and are stripped of the
fence's forbidden control/zero-width/bidi/NUL code points:

- The HTTP base client raises FIXED, status-keyed, body/URL-free messages for
  every non-2xx status, transport fault, and non-JSON parse failure. It no
  longer interpolates the request URL (which on the ERepo SEPIO path carries an
  upstream-supplied `uuid`) or the parser/transport exception text into the
  caller-visible message; the original exception stays on the `from` chain for
  operator tracebacks, and no upstream body is written to any log.
- A new `sanitize_message` primitive (in `mcp/untrusted_content.py`) strips the
  fence's forbidden code points and length-caps caller-visible free-text
  guidance; it backstops the MCP envelope `message`. Surfaces that carry an
  exception's own text, a filesystem path, or a caller-controlled name are given
  FIXED/redacted values instead (code-point stripping alone leaves the prose):
  - `get_diagnostics` snapshot `detail` is now the fixed `"Snapshot unavailable."`
    (previously raw `str(exc)`, which could carry the snapshot filesystem path);
    the exception type is recorded operator-side only.
  - The argument-validation error frame now emits FIXED per-error reasons keyed on
    the validation error type and redacts caller-supplied unexpected-argument
    names. The interceptor also now catches FastMCP 3.x's own
    `fastmcp.exceptions.ValidationError` (walking `__cause__` for the structured
    pydantic errors) — previously it caught only the pydantic type, so FastMCP's
    default arg-validation error reached the caller and echoed the offending
    argument name/value (which `mask_error_details` does not cover).
  - The output-schema-drift log event no longer includes the raw SDK validation
    message (only the tool name + the allow-listed schema field name).

Research use only; not clinical decision support.

## [3.0.0] - 2026-07-11

### BREAKING

Response-Envelope Standard v1.1 untrusted-content fencing. Every externally
sourced ClinGen free-text field now emits the typed `untrusted_text` object
(`kind` / `text` / `provenance` / `raw_sha256`) instead of a bare string, so
hosts and the router treat retrieved prose as opaque data rather than
instructions (defense in depth; research use only, mirrors the ClinGen
disclaimer). Reshaped fields, by tool:

- `get_variant_interpretation` `interpretation.summary`
- `get_variant_interpretations` `records[*].summary` (full/standard modes)
- `get_gene_dosage` / `search_dosage` `records[*].haplo_description` and
  `records[*].triplo_description` (full/standard modes)
- `get_cspec` / `get_cspec_criterion` `record(s).criteria[*].description`
  (and each `criteria[*].strengths[*].description`)
- `get_gene_validity` / `search_validity` `records[*].disease_name`
- `get_gene_summary` `validity[*].disease_name`,
  `dosage[*].haplo_description`, `dosage[*].triplo_description`
- `get_gene_actionability` `records[*].sepio_detail` (with `include_detail=true`):
  the whole live SEPIO assertion document — previously passed through raw and
  unbounded — is now fenced as one opaque `untrusted_text` object (its `docId` and
  every nested prose field live inside the typed `.text`, not as sibling keys).

No fenced field is duplicated in a sibling plain-string field. In particular the
gene-validity `recommended_citation` no longer embeds the raw `disease_name`; it
references the disease by its curated MONDO id instead (the human-readable label
still travels, as typed data, in the fenced `disease_name`). Each fenced field —
including inside array `items` — is declared as the typed `untrusted_text` object
(with the `kind` literal) in the tool's `output_schema`.

A new `clingen_link/mcp/untrusted_content.py` module (copied verbatim from the
fleet's released PubTator reference) provides the fence plus an explicit
`enforce_untrusted_text_limits` guard (2 MiB/object, 8 MiB/response, and a
per-tool object-count ceiling — 10000 for list-bearing tools, 128 for the
single-record `get_variant_interpretation`). Exceeding a ceiling raises a typed
`UntrustedTextLimitError`, surfaced as a distinct `response_too_large` envelope
(never a generic `validation_failed` / `internal_error`), rather than silently
truncating. The fence is the ONLY sanitation applied to the fenced value: `disease_name`
is now carried through the response model verbatim (its former model-level `strip_html`
pass was removed) so `raw_sha256` digests the raw upstream bytes and no prose is
regex-deleted, per the v1.1 rules. Obsolescence is still surfaced as the structured
`disease_obsolete` boolean derived from the raw label. (The offline ETL build still strips
presentational HTML from `disease_name` when assembling the snapshot corpus — a separate
ingest concern, distinct from the MCP output-boundary fence.)

## [2.0.7] - 2026-07-11

### Security

Re-enabled FastMCP 3.4.4 strict Host/Origin (DNS-rebinding) protection with
configurable allowlists. `ALLOWED_HOSTS` / `ALLOWED_ORIGINS` gate incoming
requests at the transport boundary (default loopback); set `ALLOWED_HOSTS` to
the proxied public host when deploying behind a reverse proxy or router
federation, otherwise requests are rejected with a 421.

## [2.0.6] - 2026-07-07

Security: stop the cross-session diagnostics rings from retaining caller free
text. `record_mcp_error` no longer stores the exception message / raw string
(only `tool_name`, `error_code`, and the exception class name), and
`record_schema_drift` no longer stores the raw SDK output-validation message
(only `tool_name` and the parsed schema `error_field`). Both rings are surfaced
verbatim by `get_diagnostics` to any caller, so a raw `str(exc)` or SDK message
could embed another session's query or response values and leak across sessions.
The full detail is still emitted operator-side on structured LOG lines.

## [2.0.5] - 2026-07-05

Harden ClinGen permalink test assertion to exact host match (clears CodeQL
py/incomplete-url-substring-sanitization).

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
