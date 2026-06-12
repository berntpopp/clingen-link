"""HTTP fetchers for the ClinGen Criteria Specification Registry (cspec domain).

Catalog comes from the documented paged list endpoint (non-``/api/``); structured
criteria from the per-spec JSON-LD (``/api/.../id/<GN>``); attachment links from the
rendered doc page; file metadata from a HEAD request.

These functions hit the live ``cspec.genome.network`` registry and are invoked
solely by ``clingen-link refresh`` — **never** on the MCP request path. They raise
:class:`SourceFetchError` (tagged ``source="cspec"``) on transport errors or
non-2xx responses, so the orchestrator can skip the domain and continue.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..exceptions import SourceFetchError
from .fetch import _get  # reuse the shared error-wrapping GET

_CSPEC_BASE = "https://cspec.genome.network"
_CATALOG_URL = f"{_CSPEC_BASE}/cspec/SequenceVariantInterpretation/id"
_TIMEOUT = httpx.Timeout(120.0, connect=30.0)


def fetch_catalog(
    client: httpx.Client | None = None, *, page_size: int = 250
) -> list[dict[str, Any]]:
    """Return the full SVI catalog (paged; ``pgSize`` max is 250)."""
    page_size = max(1, page_size)
    owned = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            url = f"{_CATALOG_URL}?pg={page}&pgSize={page_size}&detail=low"
            payload = _get(client, url, "cspec").json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise SourceFetchError("cspec: unexpected catalog shape", source="cspec")
            out.extend(data)
            if len(data) < page_size:
                return out
            page += 1
    finally:
        if owned:
            client.close()


def fetch_spec_jsonld(client: httpx.Client, gn_id: str) -> dict[str, Any]:
    """Return one spec's JSON-LD document."""
    url = f"{_CSPEC_BASE}/cspec/api/SequenceVariantInterpretation/id/{gn_id}"
    payload = _get(client, url, "cspec").json()
    if not isinstance(payload, dict):
        raise SourceFetchError(f"cspec: bad JSON-LD for {gn_id}", source="cspec")
    return payload


def fetch_doc_page(client: httpx.Client, gn_id: str) -> str:
    """Return the rendered doc-page HTML (carries attachment links)."""
    url = f"{_CSPEC_BASE}/cspec/ui/svi/doc/{gn_id}"
    return _get(client, url, "cspec").text


def head_file(client: httpx.Client, url: str) -> dict[str, str]:
    """Return lower-cased response headers for an attachment URL (HEAD)."""
    try:
        resp = client.head(url, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SourceFetchError(
            f"cspec: HTTP {exc.response.status_code} from {url}", source="cspec"
        ) from exc
    except httpx.HTTPError as exc:
        raise SourceFetchError(f"cspec: {exc}", source="cspec") from exc
    return {k.lower(): v for k, v in resp.headers.items()}
