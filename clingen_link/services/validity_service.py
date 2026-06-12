"""Gene-disease validity service (snapshot-only, cached)."""

from __future__ import annotations

from async_lru import alru_cache

from ..models.models import ValidityAssertion
from ..store import queries
from ..store.db import Store


class ValidityService:
    """Read + cache validity assertions from the snapshot."""

    def __init__(self, store: Store, *, cache_size: int = 256, cache_ttl_s: float = 3600) -> None:
        """Wire the store and configure the per-gene LRU+TTL cache."""
        self._store = store
        self._for_gene_cached = alru_cache(maxsize=cache_size, ttl=cache_ttl_s)(self._for_gene_impl)

    async def for_gene(
        self, symbol: str, *, classification: str | None = None, moi: str | None = None
    ) -> list[ValidityAssertion]:
        """Return validity assertions for ``symbol`` (cached when unfiltered)."""
        if classification is None and moi is None:
            return list(await self._for_gene_cached(symbol))
        with self._store.connection() as conn:
            rows = queries.validity_for_gene(conn, symbol, classification=classification, moi=moi)
        return [ValidityAssertion.from_row(r) for r in rows]

    async def _for_gene_impl(self, symbol: str) -> tuple[ValidityAssertion, ...]:
        with self._store.connection() as conn:
            rows = queries.validity_for_gene(conn, symbol)
        return tuple(ValidityAssertion.from_row(r) for r in rows)

    async def search(
        self,
        *,
        text: str | None = None,
        mondo: str | None = None,
        gene: str | None = None,
        expert_panel: str | None = None,
        classification: str | None = None,
        moi: str | None = None,
        page: int = 1,
        size: int = 25,
    ) -> tuple[list[ValidityAssertion], int]:
        """Search validity; returns ``(models, total)`` for truncation handling."""
        with self._store.connection() as conn:
            rows, total = queries.search_validity(
                conn,
                text=text,
                mondo=mondo,
                gene=gene,
                expert_panel=expert_panel,
                classification=classification,
                moi=moi,
                page=page,
                size=size,
            )
        return [ValidityAssertion.from_row(r) for r in rows], total
