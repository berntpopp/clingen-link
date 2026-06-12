"""Live drift tests against ClinGen upstream endpoints.

These hit the real ClinGen APIs to detect upstream schema/volume drift before it
breaks the snapshot ETL or the live drill-down client. They are marked
``@pytest.mark.integration`` and are excluded from the default ``make test-fast``
path; run them with ``make test-integration`` or ``uv run pytest -m integration``.

Network tolerance: every test skips (rather than fails) with a clear reason when
a host is unreachable, so a transient ClinGen outage never turns CI red. They use
the ETL fetchers / httpx directly against the configured upstream bases, so a
change to ``CLINGEN_LINK_*_API_BASE`` is respected.

Invariants asserted (as of 2026-06-12):
  * validity total       >= 3000 assertions
  * dosage total         >= 2000 records (gene + region, both builds)
  * actionability brief  parseable and >= 150 docs
  * ERepo ``news`` top entry has a ``relatedVersion``
  * ERepo ``?gene=BRCA1&format=json`` returns >= 1 interpretation
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from clingen_link.api import ClingenClient
from clingen_link.config import settings
from clingen_link.etl import fetch, parse
from clingen_link.exceptions import SourceFetchError

pytestmark = pytest.mark.integration

# Generous timeout: the ERepo bulk TSV and validity JSON are multi-MB payloads.
_TIMEOUT = httpx.Timeout(180.0, connect=30.0)


def _skip_if_unreachable(exc: Exception, host: str) -> None:
    """Skip the test with a clear reason when ``host`` is unreachable."""
    pytest.skip(f"ClinGen host {host!r} unreachable ({type(exc).__name__}: {exc})")


def test_validity_total_at_least_3000() -> None:
    """The gene-disease validity API still returns the full assertion set."""
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            rows = fetch.fetch_validity(client)
    except (SourceFetchError, httpx.HTTPError) as exc:
        _skip_if_unreachable(exc, settings.validity_api_base)

    assert isinstance(rows, list)
    assert len(rows) >= 3000, f"validity rows dropped to {len(rows)} (<3000): possible drift"
    # The parser must still cope with the live shape.
    parsed = parse.parse_validity(rows)
    assert len(parsed) >= 3000


def test_dosage_total_at_least_2000() -> None:
    """Gene + region dosage TSVs (both builds) still total >= 2000 records."""
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            bundle = fetch.fetch_dosage(client)
    except (SourceFetchError, httpx.HTTPError) as exc:
        _skip_if_unreachable(exc, settings.dosage_ftp_base)

    rows = parse.parse_dosage(bundle.gene_tsv, bundle.region_tsv)
    assert len(rows) >= 2000, f"dosage records dropped to {len(rows)} (<2000): possible drift"


def test_actionability_brief_parseable_and_at_least_150_docs() -> None:
    """The actionability ``brief`` index is parseable and still has >= 150 docs."""
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            brief = fetch.fetch_actionability(client)
    except (SourceFetchError, httpx.HTTPError) as exc:
        _skip_if_unreachable(exc, settings.actionability_api_base)

    assert isinstance(brief, list)
    parsed = parse.parse_actionability(brief)
    assert len(parsed) >= 150, f"actionability docs dropped to {len(parsed)} (<150): possible drift"


def test_erepo_news_top_entry_has_related_version() -> None:
    """The ERepo ``news`` feed's most recent entry still carries a relatedVersion."""
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            bundle = fetch.fetch_erepo(client)
    except (SourceFetchError, httpx.HTTPError) as exc:
        _skip_if_unreachable(exc, settings.erepo_api_base)

    news = bundle.news
    assert isinstance(news, list) and news, "ERepo news feed was empty: possible drift"
    top = news[0]
    assert isinstance(top, dict)
    related = top.get("relatedVersion")
    assert related, f"ERepo news top entry missing relatedVersion (keys: {sorted(top)})"


def test_erepo_gene_query_returns_interpretations() -> None:
    """ERepo classifications ``?gene=BRCA1&format=json`` returns >= 1 interpretation."""
    url = f"{settings.erepo_api_base}/api/classifications"
    params = {"gene": "BRCA1", "format": "json"}
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload: Any = response.json()
    except httpx.HTTPError as exc:
        _skip_if_unreachable(exc, settings.erepo_api_base)

    items = _interpretation_list(payload)
    assert len(items) >= 1, "ERepo gene=BRCA1 query returned no interpretations: possible drift"


def _interpretation_list(payload: Any) -> list[dict[str, Any]]:
    """Extract interpretations from a classifications payload (drift-tolerant).

    Delegates to the production client's extractor so the test and the live
    drill-down path stay in lockstep about the recognized wrapper keys (the
    real endpoint wraps results under ``variantInterpretations``).
    """
    return ClingenClient._interpretation_list(payload)
