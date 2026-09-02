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

### Fleet deploy contract

`docker/docker-compose.npm.yml` is the file the GeneFoundry fleet controller
(`strato_v6_docker_npm`) deploys and validates. Every service in it —
`clingen_data_init` and `clingen_link` — declares a numeric, non-root
`user: "10001:10001"`, matching this image's own `USER 10001:10001` from
`docker/Dockerfile`; the value is never copied from a sibling repo, since
sibling `-link` images use their own uid:gid. `user` must NOT appear in the
Compose files listed in `container-release.json`
(`docker-compose.yml`, `docker-compose.prod.yml`) — the shared release gate
forbids it there. `tests/unit/test_compose_hardening.py` guards both sides of
this contract. Reproduce the controller's own projection check locally:

```bash
export CLINGEN_LINK_IMAGE="ghcr.io/berntpopp/clingen-link@sha256:<digest>"
docker compose -f docker/docker-compose.npm.yml config --format json > /tmp/clingen-link-rendered.json
cd <path-to-strato_v6_docker_npm> && uv run python -c "
import sys, json; sys.path.insert(0, 'scripts')
from utils.deployment_preflight import canonical_projection
p = canonical_projection(json.load(open('/tmp/clingen-link-rendered.json')), project='clingen-link')
for n, s in p['services'].items(): print(n, 'user=', s.get('user'))
print('PROJECTION OK')"
```

The same overlay is gated centrally at release time. The release workflow pins
`genefoundry-router/.github/workflows/_container-release.yml@31ea81ce…` (v0.8.5), whose
`validate-deployed-overlay` step renders this file and refuses the release on any
violation. Run it yourself against a router checkout — it must exit 0:

```bash
cd <path-to-genefoundry-router> && uv run python scripts/container_release.py \
  validate-deployed-overlay --config <path-to-clingen-link>/container-release.json \
  --project-dir <path-to-clingen-link>
```

Three facts that gate makes non-negotiable here:

- **Restart policy.** `clingen_link` is `restart: unless-stopped`, not `on-failure`: an
  `on-failure` container does not come back after a host reboot or a Docker upgrade. The
  run-once `clingen_data_init` stays `restart: "no"`.
- **Declared binds.** `/seed` is the only host bind, it is `read_only: true`, and it is
  listed in `container-release.json` `service.deployed_seed_binds`. An undeclared bind is
  refused. `service.deployed_compose_files` names exactly the file set the controller
  deploys (`["docker/docker-compose.npm.yml"]`).
- **Selectable reference volume.** `volumes.clingen_reference.name` is
  `"${CLINGEN_REFERENCE_VOLUME:-clingen-link-npm_clingen_reference}"`. The default is the
  name Compose derives on its own from the project name, so leaving the variable unset
  changes nothing on a running host. It exists so the fleet controller can create a
  *candidate* volume, run `clingen-data-init` into it, verify it, and switch the server
  onto it as one reviewed data-activation step rather than mutating the live volume.

The controller also proves the data identity independently. `container-release.json`
`data.digest` is the SHA-256 of the **canonical JSON bytes** of the runtime
`data-identity-manifest.json` written into the selected version directory (the file
content without its trailing newline) — not the compressed `.zst` digest and not the
expanded-tree digest. Its semantic probe opens `/data/current/clingen.sqlite` read-only and
immutable and reads `meta.snapshot_version` (the bare `"2"` stamp, not the `"2.0.0"`
deployment pin), `COUNT(*) FROM gene`, and the SHA-256 of the first `symbol`.

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
