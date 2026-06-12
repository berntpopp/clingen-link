"""HTTP fetchers for ClinGen bulk sources (offline ETL only).

These functions hit the live ClinGen endpoints captured in
``.research/api-findings.md`` / ``.research/clingen-data-sources.md``. They are
invoked solely by ``clingen-link refresh`` — **never** on the MCP request path.

All fetchers share a generous timeout (the validity pull alone is ~1.8 MB and a
few seconds) and raise :class:`SourceFetchError` (tagged with the failing
source) on transport errors or non-2xx responses, so the orchestrator can skip
a single down domain and continue.

Each fetcher returns plain Python data (lists/dicts/str) ready for the pure
parsers, plus — for dosage — the per-file ``ETag`` / ``Last-Modified`` headers
used by the freshness layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import settings
from ..exceptions import SourceFetchError

# Generous default for bulk pulls; the validity and erepo bodies are large.
_DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=30.0)

# Dosage FTP files we pull. Both assemblies are ingested: GRCh38 is canonical and GRCh37 backfills
# the second coordinate set so ``grch37`` is populated rather than always null (assessment L5).
_DOSAGE_FILES: dict[str, str] = {
    "gene_grch38": "ClinGen_gene_curation_list_GRCh38.tsv",
    "region_grch38": "ClinGen_region_curation_list_GRCh38.tsv",
    "gene_grch37": "ClinGen_gene_curation_list_GRCh37.tsv",
    "region_grch37": "ClinGen_region_curation_list_GRCh37.tsv",
}


@dataclass
class DosageBundle:
    """Fetched dosage TSVs (both assemblies) plus the per-file conditional-GET signals."""

    gene_tsv: str
    region_tsv: str
    gene_tsv_grch37: str = ""
    region_tsv_grch37: str = ""
    etags: dict[str, str] = field(default_factory=dict)


@dataclass
class ErepoBundle:
    """Fetched ERepo bulk TSV plus the parsed news feed (for the version signal)."""

    tsv_text: str
    news: list[dict[str, Any]]


def _get(client: httpx.Client, url: str, source: str, **kwargs: Any) -> httpx.Response:
    """GET ``url`` raising a tagged :class:`SourceFetchError` on any failure."""
    try:
        response = client.get(url, **kwargs)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SourceFetchError(
            f"{source}: HTTP {exc.response.status_code} from {url}", source=source
        ) from exc
    except httpx.HTTPError as exc:
        raise SourceFetchError(f"{source}: {exc}", source=source) from exc
    return response


def fetch_validity(client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Fetch all gene-disease validity rows from ``/api/validity``.

    The endpoint ignores pagination params and returns every row in one
    ``{total, rows: [...]}`` envelope.
    """
    owned = client is None
    client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT)
    try:
        url = f"{settings.validity_api_base}/validity"
        response = _get(client, url, "validity")
        payload = response.json()
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise SourceFetchError("validity: unexpected payload shape", source="validity")
        return rows
    finally:
        if owned:
            client.close()


def fetch_dosage(client: httpx.Client | None = None) -> DosageBundle:
    """Fetch the dosage gene + region TSVs for both assemblies and capture freshness headers.

    The GRCh38 ETags are the canonical freshness signal; the GRCh37 files backfill the second
    coordinate set (they update in lockstep with GRCh38).
    """
    owned = client is None
    client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT)
    try:
        texts: dict[str, str] = {}
        etags: dict[str, str] = {}
        for key, filename in _DOSAGE_FILES.items():
            url = f"{settings.dosage_ftp_base}/{filename}"
            response = _get(client, url, "dosage")
            texts[key] = response.text
            if key.endswith("grch38"):
                etag = response.headers.get("etag") or response.headers.get("last-modified") or ""
                etags[filename] = etag
        return DosageBundle(
            gene_tsv=texts["gene_grch38"],
            region_tsv=texts["region_grch38"],
            gene_tsv_grch37=texts.get("gene_grch37", ""),
            region_tsv_grch37=texts.get("region_grch37", ""),
            etags=etags,
        )
    finally:
        if owned:
            client.close()


def fetch_actionability(client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Fetch the actionability ``brief`` index (canonical per-doc records)."""
    owned = client is None
    client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT)
    try:
        url = f"{settings.actionability_api_base}/api/summ/brief"
        response = _get(client, url, "actionability")
        payload = response.json()
        if not isinstance(payload, list):
            raise SourceFetchError("actionability: expected a list of docs", source="actionability")
        return payload
    finally:
        if owned:
            client.close()


def fetch_erepo(client: httpx.Client | None = None) -> ErepoBundle:
    """Fetch the ERepo bulk TSV and the news feed (for the version signal)."""
    owned = client is None
    client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT)
    try:
        tsv_url = f"{settings.erepo_api_base}/api/summary/classifications/download"
        tsv_text = _get(client, tsv_url, "erepo").text
        news_url = f"{settings.erepo_api_base}/api/summary/news/"
        news_payload = _get(client, news_url, "erepo").json()
        news = news_payload.get("data") if isinstance(news_payload, dict) else news_payload
        if not isinstance(news, list):
            news = []
        return ErepoBundle(tsv_text=tsv_text, news=news)
    finally:
        if owned:
            client.close()


def fetch_erepo_summary(client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch the per-gene ERepo classification-count summary (gene index input)."""
    owned = client is None
    client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT)
    try:
        url = f"{settings.erepo_api_base}/api/summary/classifications/summary/gene"
        payload = _get(client, url, "erepo_summary").json()
        if not isinstance(payload, dict):
            return {}
        return payload
    finally:
        if owned:
            client.close()


def fetch_hgnc(client: httpx.Client | None = None) -> str:
    """Fetch the HGNC complete-set TSV (symbol / alias / prev-symbol / name authority)."""
    owned = client is None
    client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT)
    try:
        return _get(client, settings.hgnc_complete_set_url, "hgnc").text
    finally:
        if owned:
            client.close()


def fetch_affiliates(client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Fetch the expert-panel affiliates list (GCEPs / VCEPs + curation counts)."""
    owned = client is None
    client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT)
    try:
        url = f"{settings.validity_api_base}/affiliates"
        payload = _get(client, url, "affiliates").json()
        rows = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        return rows
    finally:
        if owned:
            client.close()
