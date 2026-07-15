"""Clinical actionability service (snapshot + live SEPIO drill-down)."""

from __future__ import annotations

from typing import Any

from async_lru import alru_cache

from ..api.clingen_client import ActionabilityContext, ClingenClient
from ..models.models import ActionabilityCuration
from ..store import queries
from ..store.db import Store


class ActionabilityService:
    """Read + cache actionability curations; fetch live SEPIO detail on demand."""

    def __init__(
        self,
        store: Store,
        client: ClingenClient,
        *,
        cache_size: int = 256,
        cache_ttl_s: float = 3600,
    ) -> None:
        """Wire the store + live client and configure the per-gene cache."""
        self._store = store
        self._client = client
        self._for_gene_cached = alru_cache(maxsize=cache_size, ttl=cache_ttl_s)(self._for_gene_impl)

    async def for_gene(self, symbol: str, *, context: str = "Adult") -> list[ActionabilityCuration]:
        """Return actionability curations that list ``symbol`` (cached)."""
        return list(await self._for_gene_cached(symbol, context))

    async def _for_gene_impl(self, symbol: str, context: str) -> tuple[ActionabilityCuration, ...]:
        with self._store.connection() as conn:
            rows = queries.actionability_for_gene(conn, symbol)
        return tuple(ActionabilityCuration.from_row(r, context=context) for r in rows)

    async def search(
        self,
        *,
        text: str | None = None,
        gene: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 25,
    ) -> tuple[list[ActionabilityCuration], int]:
        """Search actionability by disease/gene + curation status; returns ``(models, total)``."""
        with self._store.connection() as conn:
            rows, total = queries.search_actionability(
                conn, text=text, gene=gene, status=status, page=page, size=size
            )
        return [ActionabilityCuration.from_row(r) for r in rows], total

    async def sepio_detail(self, doc_id: str, context: ActionabilityContext) -> dict[str, Any]:
        """Fetch the full live SEPIO assertion document for ``doc_id``."""
        return await self._client.actionability_sepio(doc_id, context)
