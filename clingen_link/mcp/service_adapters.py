"""Lazy service-factory singletons for the clingen-link MCP facade.

The MCP tools obtain their backing services through a ``service_factory``
callable rather than importing a concrete instance, so that:

* the FastAPI host defers to ``app.state`` (per-request shared services),
* tests can inject fakes via :func:`set_services` / reset via :func:`reset_services`,
* construction is lazy (built on first use, then cached).

:func:`get_services` builds the real :class:`ClingenServices` from
``settings.snapshot_path`` on first use. A missing/unreadable snapshot raises
:class:`~clingen_link.exceptions.SnapshotUnavailableError` **at call time** (not
import time), which the MCP error boundary maps to a ``snapshot_unavailable``
envelope rather than crashing the server. Tests inject a ready-built container
with :func:`set_services` so they never touch the bundled snapshot.
"""

from __future__ import annotations

from clingen_link.services.aggregator import ClingenServices

__all__ = [
    "ClingenServices",
    "get_services",
    "reset_services",
    "set_services",
]

# Process-cached default, built lazily on first get_services() call.
_default: ClingenServices | None = None
# A test/HTTP override that takes precedence over the cached default when set.
_override: ClingenServices | None = None


def get_services() -> ClingenServices:
    """Return the active services container.

    Returns an injected override when present (see :func:`set_services`),
    otherwise the lazily-built, process-cached default built from
    ``settings.snapshot_path``.

    Raises:
        SnapshotUnavailableError: the bundled snapshot is missing/unreadable.
    """
    global _default
    if _override is not None:
        return _override
    if _default is None:
        _default = ClingenServices.from_snapshot()
    return _default


def set_services(services: ClingenServices) -> None:
    """Inject a services container (used by tests and the HTTP host)."""
    global _override
    _override = services


def reset_services() -> None:
    """Clear any injected override and the cached default singleton.

    Tests call this (via an autouse fixture) so a previous case's instance does
    not leak into the next one.
    """
    global _override, _default
    _override = None
    _default = None
