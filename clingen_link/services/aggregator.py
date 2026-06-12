"""ClingenServices aggregator: owns the store + live client + domain services.

The MCP facade injects one :class:`ClingenServices`. It is built lazily from a
snapshot path (see ``clingen_link.mcp.service_adapters``); a missing snapshot
surfaces as :class:`SnapshotUnavailableError` at build time, which the envelope
maps to ``snapshot_unavailable`` rather than crashing import.
"""

from __future__ import annotations

from pathlib import Path

from ..api.clingen_client import ClingenClient
from ..config import settings
from ..store import cspec_queries
from ..store.db import Store
from .actionability_service import ActionabilityService
from .cspec_service import CspecService
from .dosage_service import DosageService
from .erepo_service import ErepoService
from .gene_service import GeneService
from .validity_service import ValidityService


class ClingenServices:
    """Container holding the read-only store, live client, and domain services."""

    def __init__(self, store: Store, client: ClingenClient | None = None) -> None:
        """Build domain services over an open ``store`` and a live ``client``."""
        self.store = store
        self.client = client or ClingenClient()
        ttl_s = settings.cache_ttl_minutes * 60
        size = settings.cache_size
        erepo_ttl_s = settings.erepo_cache_ttl_minutes * 60
        self.validity = ValidityService(store, cache_size=size, cache_ttl_s=ttl_s)
        self.dosage = DosageService(store, cache_size=size, cache_ttl_s=ttl_s)
        self.actionability = ActionabilityService(
            store, self.client, cache_size=size, cache_ttl_s=ttl_s
        )
        self.erepo = ErepoService(store, self.client, cache_size=size, cache_ttl_s=erepo_ttl_s)
        self.cspec = CspecService(store, cache_size=size, cache_ttl_s=ttl_s)
        self.gene = GeneService(store, self.validity, self.dosage, self.actionability)

    @classmethod
    def from_snapshot(
        cls, path: str | Path | None = None, *, client: ClingenClient | None = None
    ) -> ClingenServices:
        """Build the full service container from a snapshot path.

        Raises :class:`~clingen_link.exceptions.SnapshotUnavailableError` if the
        snapshot is missing/unreadable (the caller surfaces it through the
        envelope as ``snapshot_unavailable``).
        """
        snapshot = Path(path) if path is not None else Path(settings.snapshot_path)
        store = Store(snapshot)
        return cls(store, client=client)

    async def aclose(self) -> None:
        """Close the live client and the store (idempotent)."""
        await self.client.aclose()
        self.store.close()

    def meta(self) -> dict[str, dict[str, object]]:
        """Return per-domain snapshot freshness rows (provenance for envelopes)."""
        return self.store.meta()

    def cspec_resolve_sync(self, affiliation_id: str, gene: str | None) -> list[str]:
        """Resolve affiliation(+gene) -> GN ids synchronously (snapshot read).

        Deliberate SYNCHRONOUS twin of ``CspecService.resolve_for_erepo`` (both
        call ``resolve_gn``) so the ERepo tool can resolve without awaiting inside
        its next_commands construction; do not dedupe.
        """
        with self.store.connection() as conn:
            return cspec_queries.resolve_gn(conn, affiliation_id=affiliation_id, gene=gene)
