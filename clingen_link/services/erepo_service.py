"""ERepo variant-pathogenicity service (snapshot + live fallback, version-keyed TTL).

``get_interpretation`` prefers the snapshot row and falls back to the live
classifications API when the snapshot has no match (or ``refresh=True`` forces a
live fetch for the newest evidence-code SEPIO). The live result is cached with a
TTL whose key folds in the ERepo ``news`` ``relatedVersion`` so a published
upstream version bump transparently invalidates stale entries.
"""

from __future__ import annotations

import time
from typing import Any

from async_lru import alru_cache

from ..api.clingen_client import ClingenClient
from ..exceptions import ClingenApiError, DataNotFoundError
from ..models.models import VariantInterpretation
from ..store import queries
from ..store.db import Store
from .erepo_live import erepo_live_to_row


class ErepoService:
    """Read ERepo interpretations from the snapshot, live-fallback for detail."""

    def __init__(
        self,
        store: Store,
        client: ClingenClient,
        *,
        cache_size: int = 256,
        cache_ttl_s: float = 43200,
        version_ttl_s: float = 600,
    ) -> None:
        """Wire the store + client; configure the version-keyed live cache.

        ``cache_ttl_s`` defaults to 12h (matches ``erepo_cache_ttl_minutes``);
        ``version_ttl_s`` bounds how often the cheap ``news`` version is re-polled.
        """
        self._store = store
        self._client = client
        self._version_ttl_s = version_ttl_s
        self._version: str = "0"
        self._version_checked_at: float = 0.0
        self._live_cached = alru_cache(maxsize=cache_size, ttl=cache_ttl_s)(self._live_impl)

    async def for_gene(
        self,
        gene: str,
        *,
        assertion: str | None = None,
        page: int = 1,
        size: int = 25,
    ) -> tuple[list[VariantInterpretation], int]:
        """Return ERepo interpretations for ``gene`` from the snapshot; paginated."""
        with self._store.connection() as conn:
            rows, total = queries.erepo_for_gene(
                conn, gene, assertion=assertion, page=page, size=size
            )
        return [VariantInterpretation.from_row(r) for r in rows], total

    async def search(
        self,
        *,
        text: str | None = None,
        gene: str | None = None,
        mondo: str | None = None,
        expert_panel: str | None = None,
        assertion: str | None = None,
        page: int = 1,
        size: int = 25,
    ) -> tuple[list[VariantInterpretation], int]:
        """Search ERepo interpretations from the snapshot; returns ``(models, total)``."""
        with self._store.connection() as conn:
            rows, total = queries.search_erepo(
                conn,
                text=text,
                gene=gene,
                mondo=mondo,
                expert_panel=expert_panel,
                assertion=assertion,
                page=page,
                size=size,
            )
        return [VariantInterpretation.from_row(r) for r in rows], total

    async def get_interpretation(
        self,
        *,
        caid: str | None = None,
        hgvs: str | None = None,
        refresh: bool = False,
    ) -> tuple[VariantInterpretation, str, str | None]:
        """Return ``(interpretation, source, notice)`` for one variant, preferring the snapshot.

        ``refresh=True`` forces the live ERepo path (newest evidence-code SEPIO). The live payload
        is normalized by :func:`erepo_live_to_row` before model construction, so a valid CAID/HGVS
        never produces a Pydantic ``ValidationError`` (assessment H1). On *any* live-path failure
        the call **degrades** to the snapshot row (``source="snapshot"`` + a ``notice``) when one
        exists, and otherwise raises :class:`ClingenApiError` (``upstream_unavailable``, retryable)
        — it never mislabels a server/upstream fault as bad input.

        Raises:
            DataNotFoundError: no snapshot row and the live lookup found nothing.
            ClingenApiError: the live fetch failed and there is no snapshot to fall back to.
        """
        snapshot = self._snapshot_lookup(caid=caid, hgvs=hgvs)
        if not refresh and snapshot is not None:
            return snapshot, "snapshot", None
        try:
            version = await self._current_version()
            row = await self._live_cached(caid or "", hgvs or "", version)
            return VariantInterpretation.from_row(row), "live", None
        except DataNotFoundError:
            if snapshot is not None:
                return (
                    snapshot,
                    "snapshot",
                    "live ERepo lookup found nothing; served snapshot record",
                )
            raise
        except Exception as exc:  # upstream/parse fault -> degrade or surface upstream_unavailable
            if snapshot is not None:
                return (
                    snapshot,
                    "snapshot",
                    f"live ERepo fetch degraded ({exc.__class__.__name__}); served snapshot record",
                )
            raise ClingenApiError(f"live ERepo fetch failed: {exc.__class__.__name__}") from exc

    def _snapshot_lookup(
        self, *, caid: str | None, hgvs: str | None
    ) -> VariantInterpretation | None:
        """Look up a single interpretation in the snapshot, or ``None``."""
        with self._store.connection() as conn:
            row = None
            if caid:
                row = queries.erepo_by_caid(conn, caid)
            elif hgvs:
                row = queries.erepo_by_hgvs(conn, hgvs)
        return VariantInterpretation.from_row(row) if row is not None else None

    async def _live_impl(self, caid: str, hgvs: str, _version: str) -> dict[str, Any]:
        """Fetch + normalize one live interpretation (``_version`` is a cache-key salt only).

        Fetches the classifications summary, then best-effort enriches it with the full SEPIO
        interpretation (evidence codes Met/Not-Met) via the summary's ``uuid``. The result is the
        normalized snapshot-row shape (:func:`erepo_live_to_row`).
        """
        summary = await self._client.erepo_interpretation(caid=caid or None, hgvs=hgvs or None)
        sepio: dict[str, Any] | None = None
        uuid = summary.get("uuid") if isinstance(summary, dict) else None
        if uuid:
            try:
                sepio = await self._client.erepo_interpretation(uuid=str(uuid))
            except Exception:  # SEPIO enrichment is best-effort; the summary still answers
                sepio = None
        return erepo_live_to_row(summary, sepio=sepio)

    async def _current_version(self) -> str:
        """Return the current ERepo ``news`` version, re-polled at most per TTL."""
        now = time.monotonic()
        if now - self._version_checked_at < self._version_ttl_s:
            return self._version
        try:
            news = await self._client.erepo_news()
        except Exception:  # version probe is best-effort; keep the last good value
            self._version_checked_at = now
            return self._version
        if news:
            top = news[0].get("relatedVersion")
            if isinstance(top, str) and top:
                self._version = top
        self._version_checked_at = now
        return self._version
