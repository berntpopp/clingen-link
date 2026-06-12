"""ClinGen live drill-down endpoints over the resilient :class:`BaseClient`.

Covers the single-record live paths the snapshot intentionally does not embed:

* ERepo variant interpretations — by CAID / HGVS / gene (classifications API) or
  by interpretation UUID (full evidence-code SEPIO);
* ERepo ``news`` — the release feed whose top ``relatedVersion`` keys the live
  drill-down cache TTL;
* actionability SEPIO documents — the full Adult / Pediatric assertion docs.

Base URLs come from :data:`clingen_link.config.settings`. All methods return raw
parsed JSON (``dict`` / ``list``); shaping into Pydantic models is the service
layer's job.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

from ..config import settings
from ..exceptions import ClingenApiError, DataNotFoundError, UpstreamInputError
from .base_client import BaseClient

ActionabilityContext = Literal["Adult", "Pediatric"]


class ClingenClient(BaseClient):
    """Live ClinGen endpoint methods layered on the resilience base client."""

    def __init__(
        self,
        *,
        erepo_base: str | None = None,
        actionability_base: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs: Any,
    ) -> None:
        """Build the client; base URLs default to settings, override for tests."""
        super().__init__(client=client, **kwargs)
        self._erepo_base = (erepo_base or settings.erepo_api_base).rstrip("/")
        self._actionability_base = (actionability_base or settings.actionability_api_base).rstrip(
            "/"
        )

    # ------------------------------------------------------------------
    # ERepo (variant pathogenicity)
    # ------------------------------------------------------------------
    async def erepo_interpretation(
        self,
        *,
        caid: str | None = None,
        hgvs: str | None = None,
        uuid: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a single ERepo variant interpretation live.

        Exactly one selector must be supplied. ``uuid`` hits the interpretation
        endpoint (full evidence-code SEPIO); ``caid`` / ``hgvs`` hit the
        classifications search and the first matching interpretation is returned.

        Raises:
            UpstreamInputError: zero or multiple selectors supplied.
            DataNotFoundError: no interpretation matched the selector.
        """
        selectors = [s for s in (caid, hgvs, uuid) if s]
        if len(selectors) != 1:
            raise UpstreamInputError(
                "erepo_interpretation requires exactly one of caid, hgvs, or uuid."
            )
        if uuid:
            url = f"{self._erepo_base}/api/interpretation/{uuid}"
            payload = await self.get_json(url, params={"format": "json"})
            return self._as_dict(payload)
        params = {"format": "json"}
        if caid:
            params["caid"] = caid
        else:
            params["hgvs"] = hgvs or ""
        url = f"{self._erepo_base}/api/classifications"
        payload = await self.get_json(url, params=params)
        return self._first_interpretation(payload, selector=caid or hgvs or "")

    async def erepo_for_gene_live(
        self, gene: str, *, match_limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Fetch ERepo interpretations for ``gene`` live (server-side gene filter)."""
        params: dict[str, Any] = {"format": "json", "gene": gene}
        if match_limit is not None:
            params["matchLimit"] = match_limit
        url = f"{self._erepo_base}/api/classifications"
        payload = await self.get_json(url, params=params)
        return self._interpretation_list(payload)

    async def erepo_news(self) -> list[dict[str, Any]]:
        """Fetch the ERepo ``news`` feed (top ``relatedVersion`` keys the TTL)."""
        url = f"{self._erepo_base}/api/summary/news/"
        payload = await self.get_json(url)
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        if isinstance(payload, list):
            return [d for d in payload if isinstance(d, dict)]
        return []

    # ------------------------------------------------------------------
    # Actionability (clinical actionability SEPIO)
    # ------------------------------------------------------------------
    async def actionability_sepio(
        self, doc_id: str, context: ActionabilityContext
    ) -> dict[str, Any]:
        """Fetch the full SEPIO assertion document for ``doc_id`` in ``context``.

        Uses the SEPIO IRI shape from the brief
        (``/ac/{Adult,Pediatric}/api/sepio/doc/{docId}``); the plain
        ``/ac/api/doc/{docId}`` path 404s upstream.
        """
        url = f"{self._actionability_base}/{context}/api/sepio/doc/{doc_id}"
        payload = await self.get_json(url)
        return self._as_dict(payload)

    # ------------------------------------------------------------------
    # Payload helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _as_dict(payload: Any) -> dict[str, Any]:
        """Coerce a JSON payload to a dict (unwrapping a single-element list)."""
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        raise ClingenApiError("Upstream returned an unexpected payload shape.")

    @staticmethod
    def _interpretation_list(payload: Any) -> list[dict[str, Any]]:
        """Extract the interpretation list from a classifications payload.

        The API has returned both a bare list and a wrapped ``{data: [...]}`` /
        ``{rows: [...]}`` shape across versions; tolerate all three.
        """
        if isinstance(payload, list):
            return [p for p in payload if isinstance(p, dict)]
        if isinstance(payload, dict):
            for key in ("data", "rows", "interpretations"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [p for p in value if isinstance(p, dict)]
        return []

    def _first_interpretation(self, payload: Any, *, selector: str) -> dict[str, Any]:
        """Return the first interpretation, or raise not-found for ``selector``."""
        items = self._interpretation_list(payload)
        if not items:
            raise DataNotFoundError(f"No ERepo interpretation found for '{selector}'.")
        return items[0]
