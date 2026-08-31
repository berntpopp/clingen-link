# syntax=docker/dockerfile:1
# Multi-stage Dockerfile for clingen-link.
#
# The application image is code-only. A separate init invocation verifies and
# materializes an operator-supplied immutable snapshot into a mounted volume.

FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS builder

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

# Pin the uv installer to a digest-addressed official image for a reproducible
# build, instead of a floating installer bootstrap (F-19).
COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab /uv /usr/local/bin/uv

RUN uv sync --frozen --no-dev --active --no-install-project


FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS prepared


# Runtime identity. The deploy builds from a source checkout rather than pulling a
# registry image, so there is no immutable OCI digest to pin; stamping the exact
# commit into the image is what lets an operator prove the running container was
# built from the revision the router's release inventory pins, instead of trusting
# a moving `main`. VCS_REF is unprefixed on purpose: a <SERVICE>_-prefixed name
# would land in the pydantic settings namespace.
ARG VCS_REF=unknown
LABEL org.opencontainers.image.source="https://github.com/berntpopp/clingen-link" \
      org.opencontainers.image.revision="${VCS_REF}"
ENV VCS_REF="${VCS_REF}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    CLINGEN_LINK_MCP_HOST=0.0.0.0 \
    CLINGEN_LINK_MCP_PORT=8000 \
    TMPDIR=/tmp

RUN apt-get update \
    && apt-get install -y --only-upgrade --no-install-recommends \
    openssl \
    libssl3t64 \
    openssl-provider-legacy \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --home /home/app --create-home app && \
    mkdir -p /data /seed && \
    chown -R app:app /data /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

# .dockerignore admits only the reviewed tiny SVI JSON code resource under data/.
COPY --chown=app:app ./clingen_link ./clingen_link
COPY --chown=app:app ./pyproject.toml ./README.md ./

RUN /opt/venv/bin/pip install . --no-deps

RUN rm -rf /usr/local/lib/python3.14/ensurepip \
           /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
           /opt/venv/lib/python3.14/site-packages/pip \
           /opt/venv/lib/python3.14/site-packages/pip-*.dist-info \
           /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.14 \
           /root/.cache/pip /home/app/web/clingen_link \
    && find /usr/local/lib/python3.14 /opt/venv -type d -name __pycache__ -prune -exec rm -rf '{}' + \
    && find / -xdev -perm /6000 -exec chmod a-s '{}' + \
    && test -z "$(find / -xdev -perm /6000 -print -quit)" \
    && actual="$(find / -xdev -type f -links +1 -printf '%i %p\n' | sort -k2,2)" \
    && expected="$(printf '%s %s\n%s %s\n' \
         "$(stat -c '%i' /usr/bin/perl)" /usr/bin/perl \
         "$(stat -c '%i' /usr/bin/perl5.40.1)" /usr/bin/perl5.40.1 | sort -k2,2)" \
    && test "$actual" = "$expected" \
    && rm -f /usr/bin/perl5.40.1

FROM scratch AS production
COPY --from=prepared / /

ARG APP_VERSION=0.0.0
ARG VCS_REF=0000000000000000000000000000000000000000
ARG BUILD_DATE=1970-01-01T00:00:00Z
LABEL org.opencontainers.image.title="ClinGen Link" \
      org.opencontainers.image.description="ClinGen curated evidence MCP server for research use" \
      org.opencontainers.image.source="https://github.com/berntpopp/clingen-link" \
      org.opencontainers.image.url="https://github.com/berntpopp/clingen-link" \
      org.opencontainers.image.documentation="https://github.com/berntpopp/clingen-link#docker" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="GeneFoundry" \
      org.genefoundry.research-use-only="true" \
      org.genefoundry.data-policy="code-only"
ENV VCS_REF="${VCS_REF}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    CLINGEN_LINK_MCP_HOST=0.0.0.0 \
    CLINGEN_LINK_MCP_PORT=8000 \
    TMPDIR=/tmp

WORKDIR /home/app/web
USER 10001:10001

EXPOSE 8000

# Unified transport: FastAPI host (/health) + MCP streamable-HTTP at /mcp on one port.
CMD ["clingen-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
