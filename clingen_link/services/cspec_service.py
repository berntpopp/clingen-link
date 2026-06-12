"""CSpec service: read criteria specifications from the snapshot (cached)."""

from __future__ import annotations

from async_lru import alru_cache

from ..models.models import CriteriaCode, CspecDetail, CspecSummary
from ..store import cspec_queries
from ..store.db import Store


class CspecService:
    """Read + cache ClinGen criteria specifications from the snapshot."""

    def __init__(self, store: Store, *, cache_size: int = 256, cache_ttl_s: float = 3600) -> None:
        """Wire the store and configure the per-spec LRU+TTL cache."""
        self._store = store
        self._detail_cached = alru_cache(maxsize=cache_size, ttl=cache_ttl_s)(self._detail_impl)

    async def list_specs(
        self,
        *,
        gene: str | None = None,
        affiliation: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 25,
    ) -> tuple[list[CspecSummary], int]:
        """List spec headers; returns ``(models, total)``."""
        with self._store.connection() as conn:
            rows, total = cspec_queries.list_cspecs(
                conn, gene=gene, affiliation=affiliation, status=status, page=page, size=size
            )
        return [CspecSummary.from_row(r) for r in rows], total

    async def get_detail(self, *, gn_id: str) -> CspecDetail | None:
        """Return one spec with genes, criteria, and files (cached)."""
        return await self._detail_cached(gn_id)

    async def _detail_impl(self, gn_id: str) -> CspecDetail | None:
        with self._store.connection() as conn:
            spec = cspec_queries.get_cspec_by_gn(conn, gn_id)
            if spec is None:
                return None
            genes = cspec_queries.get_genes(conn, gn_id)
            criteria = cspec_queries.get_criteria(conn, gn_id)
            for crit in criteria:
                crit["strengths"] = cspec_queries._strengths(conn, crit["criteria_id"])
                crit["files"] = cspec_queries.list_files(
                    conn, gn_id, criteria_id=crit["criteria_id"]
                )
            files = cspec_queries.list_files(conn, gn_id)
        return CspecDetail.assemble(spec, genes=genes, criteria=criteria, files=files)

    async def get_criterion(self, *, criteria_id: str) -> CriteriaCode | None:
        """Return one criterion (strengths + files) or ``None``."""
        with self._store.connection() as conn:
            row = cspec_queries.get_criterion(conn, criteria_id)
        return CriteriaCode.from_row(row) if row is not None else None

    async def resolve_criterion_ids(
        self, *, gn_id: str, code: str, rule_set_id: str | None = None
    ) -> list[str]:
        """Return criteria_id(s) for a ``(gn_id, code)`` pair."""
        with self._store.connection() as conn:
            return cspec_queries.resolve_criterion(conn, gn_id, code, rule_set_id=rule_set_id)

    async def search(
        self, *, text: str, page: int = 1, size: int = 25
    ) -> tuple[list[dict[str, object]], int]:
        """FTS search; returns ``(hit_rows, total)`` (each hit names its entity_type + ids)."""
        with self._store.connection() as conn:
            return cspec_queries.search_cspec(conn, text=text, page=page, size=size)

    async def resolve_for_erepo(self, *, affiliation_id: str, gene: str | None) -> list[str]:
        """Return GN ids for an ERepo affiliation (narrowed by gene when given)."""
        with self._store.connection() as conn:
            return cspec_queries.resolve_gn(conn, affiliation_id=affiliation_id, gene=gene)
