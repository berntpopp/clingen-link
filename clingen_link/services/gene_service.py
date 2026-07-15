"""Gene hub service: resolve genes + aggregate the cross-domain summary."""

from __future__ import annotations

from typing import Any

from ..models.models import ExpertPanel, GeneSummary
from ..store import queries
from ..store.db import Store
from .actionability_service import ActionabilityService
from .dosage_service import DosageService
from .validity_service import ValidityService


class GeneService:
    """Gene resolution, candidate search, and the flagship cross-domain summary."""

    # How many per-domain records the summary embeds before truncating; keeps the
    # one-call overview token-efficient.
    _SUMMARY_LIMIT = 25

    def __init__(
        self,
        store: Store,
        validity: ValidityService,
        dosage: DosageService,
        actionability: ActionabilityService,
    ) -> None:
        """Wire the store + the three snapshot domain services the summary needs."""
        self._store = store
        self._validity = validity
        self._dosage = dosage
        self._actionability = actionability

    def resolve(self, query: str) -> str | None:
        """Resolve free-text gene input to a canonical symbol (or ``None``)."""
        return self._store.resolve_gene(query)

    def search(self, query: str, *, limit: int = 25) -> tuple[list[dict[str, Any]], int]:
        """Return ``(candidates, total)`` — the capped candidate rows and the full match count.

        The count is what lets ``search_genes`` say whether more candidates exist; a capped
        list with no total reads as "this is all of them".
        """
        with self._store.connection() as conn:
            rows = queries.search_genes(conn, query, limit=limit)
            total = queries.count_genes(conn, query)
        return rows, total

    def expert_panels(self, *, query: str | None = None, limit: int = 100) -> list[ExpertPanel]:
        """Return GCEP/VCEP expert panels, optionally filtered by label text."""
        with self._store.connection() as conn:
            rows = queries.expert_panels(conn, query=query, limit=limit)
        return [ExpertPanel.from_row(r) for r in rows]

    async def get_summary(self, symbol: str) -> GeneSummary | None:
        """Aggregate all four domains for ``symbol`` into one summary, or ``None``.

        ``symbol`` must already be canonical (resolve first). Returns ``None`` if
        the gene is absent from the snapshot index.
        """
        with self._store.connection() as conn:
            counts = queries.gene_summary_counts(conn, symbol)
        if counts is None:
            return None
        validity = await self._validity.for_gene(symbol)
        dosage = await self._dosage.for_gene(symbol)
        actionability = await self._actionability.for_gene(symbol)
        return GeneSummary.from_counts(
            counts,
            validity=validity[: self._SUMMARY_LIMIT],
            dosage=dosage[: self._SUMMARY_LIMIT],
            actionability=actionability[: self._SUMMARY_LIMIT],
        )
