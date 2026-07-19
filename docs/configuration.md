# Configuration

Settings load from the environment with the `CLINGEN_LINK_` prefix (and an optional
`.env`; see [`.env.example`](../.env.example)). `uv run clingen-link config` prints the
resolved configuration; `--validate` fails closed on an incomplete one.

## Upstream endpoints

Used by the offline ETL and, for two domains, by the live drill-down path.

| Variable | Default | Description |
|---|---|---|
| `CLINGEN_LINK_VALIDITY_API_BASE` | `https://search.clinicalgenome.org/api` | Gene-disease validity API base (ETL + expert-panel affiliates). |
| `CLINGEN_LINK_DOSAGE_FTP_BASE` | `https://ftp.clinicalgenome.org` | Dosage TSV source (ETL). |
| `CLINGEN_LINK_ACTIONABILITY_API_BASE` | `https://actionability.clinicalgenome.org/ac` | Actionability API base (ETL + live SEPIO). |
| `CLINGEN_LINK_EREPO_API_BASE` | `https://erepo.clinicalgenome.org/evrepo` | ERepo API base (ETL + live drill-down). |
| `CLINGEN_LINK_HGNC_COMPLETE_SET_URL` | HGNC complete-set TSV | Authoritative symbol / alias / previous-symbol / name table. **ETL only.** |

## Snapshot and the data-bundle contract

The application is **code-only**: it never builds or downloads data at serve time. The
init service verifies an operator-supplied bundle against these exact values and
atomically selects a versioned snapshot; the server mounts it read-only
(`mode=ro&immutable=1`). See [`data.md`](data.md) for the release and refresh workflow.

| Variable | Default | Description |
|---|---|---|
| `CLINGEN_LINK_SNAPSHOT_PATH` | `/data/current/clingen.sqlite` | The selected read-only snapshot the server opens. A `.zst` path is **rejected** here — the hardened init path is the only decompressor. |
| `CLINGEN_LINK_DATA_BUNDLE_PATH` | *required* | Reviewed pre-seeded `.zst` bundle path. Used **only** by the init service. |
| `CLINGEN_LINK_DATA_BUNDLE_SHA256` | *required* | Exact compressed-bundle SHA-256. |
| `CLINGEN_LINK_DATA_EXPANDED_SHA256` | *required* | Canonical expanded-tree SHA-256. |
| `CLINGEN_LINK_DATA_RELEASE_TAG` | `data-clingen-2026-07-16` | Immutable data-release tag written into the canonical runtime identity manifest. Mutable names such as `latest` are rejected. |
| `CLINGEN_LINK_DATA_IDENTITY_DIGEST` | `sha256:9b8ef2094b31dade597b59cd2f58c3ccbba80f45e8b00d34ec6519291d2e6cbe` | Expected SHA-256 of the canonical runtime identity manifest. This is distinct from the compressed-bundle and expanded-tree digests. |
| `CLINGEN_LINK_DATA_SCHEMA_VERSION` | `2.0.0` | Exact expected snapshot schema version. |
| `CLINGEN_LINK_DATA_SCHEMA_MINIMUM` | `2.0.0` | Lower bound of the compatible schema range. |
| `CLINGEN_LINK_DATA_SCHEMA_MAXIMUM` | `2.0.0` | Upper bound of the compatible schema range. |
| `CLINGEN_LINK_DATA_MAX_COMPRESSED_BYTES` | `67108864` (64 MiB) | Compressed-bundle size ceiling (decompression-bomb guard). |
| `CLINGEN_LINK_DATA_MAX_EXPANDED_BYTES` | `268435456` (256 MiB) | Expanded-tree size ceiling. |
| `CLINGEN_LINK_DATA_ROOT` | `/data` | Writable root for immutable versioned snapshots. The only data path the container-hardening policy approves. |

An incomplete contract fails closed: `data_requirement()` raises rather than serving
unverified bytes. A digest, schema, or ceiling mismatch keeps the service **down on
purpose** — it does not degrade to a stale snapshot.

Materialization writes `data-identity-manifest.json` atomically beside the selected
`clingen.sqlite`. Its canonical v1 inventory covers every regular runtime input in that
version directory. The version-directory key includes both the bundle digest and a hash of
the immutable release tag, so materializing another tag never rewrites an existing version.
The server resolves and binds one contained version at startup; changing `/data/current`
does not change any open connection, tool, or readiness result until the process restarts.

Successful `/health` readiness includes `release_identity.data_identity.expected` from
`CLINGEN_LINK_DATA_RELEASE_TAG` / `CLINGEN_LINK_DATA_IDENTITY_DIGEST` and independently
verified `actual` values from that bound manifest. Missing, corrupt, extra, or mismatched
runtime inputs produce HTTP 503 with `status: "degraded"`; no partial `release_identity` is
emitted on failure.

## Live client resilience

| Variable | Default | Description |
|---|---|---|
| `CLINGEN_LINK_MAX_CONCURRENCY` | `5` | Max concurrent in-flight upstream requests; bounds burst pressure on ClinGen. |
| `CLINGEN_LINK_REQUEST_TIMEOUT_S` | `30` | Per-request upstream timeout (seconds). |
| `CLINGEN_LINK_QUEUE_WAIT_TIMEOUT_S` | `20` | Max wait for a concurrency slot before returning a fast, retryable `rate_limited` error instead of hanging. |

## Caching

| Variable | Default | Description |
|---|---|---|
| `CLINGEN_LINK_CACHE_SIZE` | `512` | Service-layer LRU cache size. |
| `CLINGEN_LINK_CACHE_TTL_MINUTES` | `60` | General service cache TTL. |
| `CLINGEN_LINK_EREPO_CACHE_TTL_MINUTES` | `720` | ERepo live drill-down cache TTL. Longer because it is keyed to the ERepo `news` version, which changes infrequently. |

## Transport

Streamable HTTP **only**. There is no stdio transport.

| Variable | Default | Description |
|---|---|---|
| `CLINGEN_LINK_MCP_TRANSPORT` | `unified` | `unified` (FastAPI `/health` host + mounted MCP HTTP) or its `http` alias. |
| `CLINGEN_LINK_MCP_HOST` | `127.0.0.1` | Bind host. |
| `CLINGEN_LINK_MCP_PORT` | `8000` | Bind port. |
| `CLINGEN_LINK_MCP_PATH` | `/mcp` | MCP endpoint path (a leading `/` is added if missing). |
| `CLINGEN_LINK_ALLOWED_HOSTS` | `["localhost","127.0.0.1","::1"]` | Exact accepted `Host` values. Add the public reverse-proxy hostname. **Wildcards are rejected** — pattern syntax makes the boundary ambiguous. |
| `CLINGEN_LINK_ALLOWED_ORIGINS` | `[]` | Exact accepted browser `Origin` values. Requests without an `Origin` header remain allowed. |

## Logging

| Variable | Default | Description |
|---|---|---|
| `CLINGEN_LINK_LOG_LEVEL` | `INFO` | Application log level. |
| `CLINGEN_LINK_MCP_LOG_LEVEL` | `INFO` | MCP-layer log level. |
| `CLINGEN_LINK_LOG_FORMAT` | `json` | Renderer: `json` (production) or `console` (dev). JSON by default per the GeneFoundry Logging Standard v1. |

Logging is `structlog` with `asgi-correlation-id`, so every request carries a correlation id.

## Server

| Variable | Default | Description |
|---|---|---|
| `CLINGEN_LINK_CORS_ORIGINS` | `*` | Comma-separated CORS **response** origins. |
| `CLINGEN_LINK_ENABLE_SWAGGER` | `true` | Serve the FastAPI docs routes. |
| `CLINGEN_LINK_ENABLE_MONITORING` | `true` | Serve Prometheus metrics. |
| `CLINGEN_LINK_GRACEFUL_SHUTDOWN_TIMEOUT` | `30` | Graceful shutdown timeout (seconds). |
| `CLINGEN_LINK_MAX_PAGE_SIZE` | `100` | Maximum `size` for search/list tools. |

> **Origin request validation is separate from CORS response headers.** Browser-facing
> deployments must configure the same public HTTPS origin in **both**
> `CLINGEN_LINK_ALLOWED_ORIGINS` (the request-admission gate) and
> `CLINGEN_LINK_CORS_ORIGINS` (the response header). CORS headers do not replace request
> validation.

## CLI flags

`clingen-link serve` flags override the environment for a single invocation:
`--transport` (`unified` | `http`), `--host`, `--port`, `--mcp-path`, `--log-level`,
`--disable-docs`, `--dev` (console logs).

The single `typer` app also exposes `config`, `health --url`, `refresh`,
`materialize-data`, and `version`. Run `uv run clingen-link --help` for the full list.

## MCP client configuration

clingen-link is Streamable-HTTP only, so a client points at the `/mcp` endpoint of a
running unified server:

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

Behind the [`genefoundry-router`](https://github.com/berntpopp/genefoundry-router) gateway,
clients connect to the gateway rather than to this server directly.
