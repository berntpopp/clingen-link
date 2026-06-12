"""Domain services: cache + merge the read-only store with the live client.

Each per-domain service wraps the :class:`~clingen_link.store.db.Store` (and, for
ERepo, the :class:`~clingen_link.api.clingen_client.ClingenClient`) and returns
typed Pydantic models with citations. :class:`ClingenServices` is the aggregator
the MCP facade injects; it owns one ``Store`` + one ``ClingenClient`` and exposes
the five domain services.
"""

from __future__ import annotations

from .aggregator import ClingenServices

__all__ = ["ClingenServices"]
