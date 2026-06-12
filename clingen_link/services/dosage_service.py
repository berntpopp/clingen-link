"""Gene dosage service (snapshot-only, cached)."""

from __future__ import annotations

from async_lru import alru_cache

from ..models.models import DosageRecord
from ..store import queries
from ..store.db import Store


class DosageService:
    """Read + cache dosage records from the snapshot."""

    def __init__(self, store: Store, *, cache_size: int = 256, cache_ttl_s: float = 3600) -> None:
        """Wire the store and configure the per-gene LRU+TTL cache."""
        self._store = store
        self._for_gene_cached = alru_cache(maxsize=cache_size, ttl=cache_ttl_s)(self._for_gene_impl)

    async def for_gene(self, symbol: str) -> list[DosageRecord]:
        """Return dosage records for gene ``symbol`` (cached)."""
        return list(await self._for_gene_cached(symbol))

    async def _for_gene_impl(self, symbol: str) -> tuple[DosageRecord, ...]:
        with self._store.connection() as conn:
            rows = queries.dosage_for_gene(conn, symbol)
        return tuple(DosageRecord.from_row(r) for r in rows)

    async def search(
        self,
        *,
        text: str | None = None,
        isca_id: str | None = None,
        cytoband: str | None = None,
        haplo_score: str | None = None,
        triplo_score: str | None = None,
        record_type: str | None = None,
        page: int = 1,
        size: int = 25,
    ) -> tuple[list[DosageRecord], int]:
        """Search dosage (gene + region); returns ``(models, total)``."""
        with self._store.connection() as conn:
            rows, total = queries.search_dosage(
                conn,
                text=text,
                isca_id=isca_id,
                cytoband=cytoband,
                haplo_score=haplo_score,
                triplo_score=triplo_score,
                record_type=record_type,
                page=page,
                size=size,
            )
        return [DosageRecord.from_row(r) for r in rows], total
