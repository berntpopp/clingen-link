# clingen-link Docker Deployment

Production-ready Docker setup for clingen-link with a multi-stage build, a
non-root runtime user, and Compose overlays for local, development, production,
and Nginx Proxy Manager deployments. The server runs a single **unified**
process that exposes the FastAPI host (`/health`) and the MCP streamable-HTTP
endpoint (`/mcp`) over one port.

The image contains code only. An init service verifies an exact compressed
digest, bounded expanded-tree digest, and schema version for an operator-supplied
bundle, then atomically selects a versioned snapshot. The application mounts the
selected SQLite database read-only and opens it with `mode=ro&immutable=1`.

## Quick start

```bash
make docker-build
CLINGEN_LINK_DATA_BUNDLE_PATH=/srv/data/clingen.sqlite.zst \
CLINGEN_LINK_DATA_BUNDLE_SHA256=<sha256> \
CLINGEN_LINK_DATA_EXPANDED_SHA256=<expanded-tree-sha256> make docker-up
curl http://localhost:8000/health
make docker-down
```

By default the base compose publishes host port `8000` (mapped to the standard
container port `8000`). Override it when running beside sibling projects:

```bash
CLINGEN_LINK_HOST_PORT=8120 make docker-up
```

## Compose files

- `docker-compose.yml` — base unified service, published on host port 8000.
- `docker-compose.dev.yml` — development service with source bind mounts.
- `docker-compose.prod.yml` — production hardening overlay (no host ports,
  read-only root, dropped capabilities, resource limits).
- `docker-compose.npm.yml` — Nginx Proxy Manager deployment (no host ports).

Layer overlays explicitly:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml config
```

## Local development

```bash
docker compose -f docker/docker-compose.dev.yml up --build
curl http://localhost:8000/health
```

The dev compose mounts source for iteration; use the base init service or an
already materialized local reference volume for data.

## Standalone unified server

```bash
docker compose -f docker/docker-compose.yml up -d --build
curl http://localhost:8000/health
curl http://localhost:8000/mcp
docker compose -f docker/docker-compose.yml logs -f
```

## Production overlay

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.prod.yml \
  up -d
```

The production overlay follows the sibling repository pattern:

- no published host ports by default,
- read-only root filesystem,
- small writable tmpfs for process scratch space,
- no-egress init service and read-only reference mount,
- `no-new-privileges`,
- Linux capabilities dropped,
- PID limit and init process,
- resource limits and JSON log rotation,
- unified server serving the FastAPI host and MCP on a single port.

The MCP streamable-HTTP endpoint at `/mcp` is session-aware, so a plain
`GET /mcp` probe can return protocol errors. The Compose health check probes
`/health` over HTTP instead; use an MCP client for protocol-level verification.

HTTP requests use exact Host and Origin allowlists. Browser deployments must set
the same public HTTPS origin in `CLINGEN_LINK_ALLOWED_ORIGINS` and
`CLINGEN_LINK_CORS_ORIGINS`; CORS headers do not replace request validation.

## Nginx Proxy Manager

1. Copy and edit the Docker environment file:

   ```bash
   cp .env.docker.example .env.docker
   ```

2. Ensure the external NPM network exists. The default is `npm_default`; change
   `NPM_SHARED_NETWORK_NAME` in `.env.docker` if your deployment uses another
   network.

3. Start the unified container:

   ```bash
   docker compose \
     --env-file .env.docker \
     -f docker/docker-compose.npm.yml \
     up -d
   ```

4. In Nginx Proxy Manager, proxy `/health` and `/mcp` to `clingen-link-npm:8000`.
   Enable Websockets Support, Block Common Exploits, and Force SSL after
   certificate issuance.

## Image build notes

The Dockerfile uses a multi-stage `uv` build:

- the builder stage installs production dependencies into `/opt/venv`,
- the runtime stage contains only the virtual environment and application code,
- the runtime user is non-root (`app`),
- package installs use the checked-in `uv.lock` (`uv sync --frozen --no-dev`).

No secrets are copied into the image. Pass environment-specific settings through
Compose `env_file` or environment variables at runtime.

## Refreshing the snapshot

The weekly `data-refresh.yml` workflow builds snapshot assets without credentials.
An explicitly authorized publisher verifies the handoff, attests exact bytes,
and publishes a draft-first immutable `data-clingen-YYYY-MM-DD` release. Deploys
pin both compressed and canonical expanded-tree digests.

## Troubleshooting

**Port conflicts** — set `CLINGEN_LINK_HOST_PORT` to another free port.

**Snapshot identity mismatch** — verify the configured release, compressed and
expanded-tree digests, and schema version; the service intentionally stays down.

**NPM network missing**

```bash
docker network ls
docker network create npm_default
```

**Build cache issues**

```bash
docker compose -f docker/docker-compose.yml build --no-cache
```
