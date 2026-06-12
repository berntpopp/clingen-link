"""Lazy service-factory singletons for the clingen-link MCP facade.

The MCP tools obtain their backing services through a ``service_factory``
callable rather than importing a concrete instance, so that:

* HTTP mode can defer to ``app.state`` and stdio mode can hold a direct instance,
* tests can inject fakes via :func:`set_services` / reset via :func:`reset_services`,
* construction is lazy (built on first use, then cached).

Phase 1 ships a minimal :class:`ClingenServices` placeholder. The discovery and
diagnostics tools do not yet need a backing data layer, so the default factory
returns a None-tolerant stub. Later phases replace this with the real store +
domain services.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass
class ClingenServices:
    """Placeholder service container for Phase 1.

    Later phases add the read-only store and per-domain services as fields. The
    container is intentionally None-tolerant so Phase 1 tools can be registered
    and called without a built snapshot.
    """

    store: object | None = None


@lru_cache(maxsize=1)
def _build_default_services() -> ClingenServices:
    """Build the default services container once per process (cached)."""
    return ClingenServices()


# A test/HTTP override that takes precedence over the cached default when set.
_override: ClingenServices | None = None


def get_services() -> ClingenServices:
    """Return the active services container.

    Returns an injected override when present (see :func:`set_services`),
    otherwise the lazily-built, process-cached default.
    """
    if _override is not None:
        return _override
    return _build_default_services()


def set_services(services: ClingenServices) -> None:
    """Inject a services container (used by tests and the HTTP host)."""
    global _override
    _override = services


def reset_services() -> None:
    """Clear any injected override and the cached default singleton.

    Tests call this (via an autouse fixture) so a previous case's instance does
    not leak into the next one.
    """
    global _override
    _override = None
    _build_default_services.cache_clear()
