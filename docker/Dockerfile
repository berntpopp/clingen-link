# syntax=docker/dockerfile:1
# Multi-stage Dockerfile for clingen-link.
#
# The bundled, read-only SQLite snapshot (clingen_link/data/clingen.sqlite.zst)
# is copied into the runtime image so the server works offline out of the box.

FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv" \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /home/app/web
COPY uv.lock pyproject.toml README.md ./

RUN pip install --upgrade pip uv && \
    uv sync --frozen --no-dev --active --no-install-project


FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    CLINGEN_LINK_MCP_HOST=0.0.0.0 \
    CLINGEN_LINK_MCP_PORT=8000 \
    TMPDIR=/tmp/clingen-link

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --system app && \
    useradd --system --gid app --home /home/app --create-home app && \
    mkdir -p /tmp/clingen-link && \
    chown -R app:app /tmp/clingen-link /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

# Application code plus the bundled snapshot .zst (and its .sha256) ship inside
# the package directory, so this COPY carries the data into the image.
COPY --chown=app:app ./clingen_link ./clingen_link
COPY --chown=app:app ./pyproject.toml ./README.md ./

RUN /opt/venv/bin/pip install . --no-deps

USER app

EXPOSE 8000

# Unified transport: FastAPI host (/health) + MCP streamable-HTTP at /mcp on one port.
CMD ["clingen-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
