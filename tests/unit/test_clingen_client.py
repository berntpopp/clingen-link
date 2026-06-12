"""Tests for the live ClinGen httpx client + resilience layer (respx)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from clingen_link.api.clingen_client import ClingenClient
from clingen_link.exceptions import (
    ClingenApiError,
    DataNotFoundError,
    RateLimitedError,
    UpstreamInputError,
)

_EREPO = "https://erepo.test/evrepo"
_ACTION = "https://actionability.test/ac"


@pytest.fixture
async def client() -> AsyncIterator[ClingenClient]:
    """A ClingenClient pointed at test bases with fast retry budgets."""
    c = ClingenClient(
        erepo_base=_EREPO,
        actionability_base=_ACTION,
        timeout_s=1.0,
        queue_wait_timeout_s=1.0,
        max_concurrency=3,
    )
    try:
        yield c
    finally:
        await c.aclose()


class TestErepo:
    @respx.mock
    async def test_interpretation_by_caid(self, client: ClingenClient) -> None:
        route = respx.get(f"{_EREPO}/api/classifications").mock(
            return_value=httpx.Response(200, json=[{"caid": "CA1", "gene": "BRAF"}])
        )
        result = await client.erepo_interpretation(caid="CA1")
        assert result["gene"] == "BRAF"
        assert route.calls.last.request.url.params["caid"] == "CA1"
        assert route.calls.last.request.url.params["format"] == "json"

    @respx.mock
    async def test_interpretation_by_uuid(self, client: ClingenClient) -> None:
        respx.get(f"{_EREPO}/api/interpretation/abc-123").mock(
            return_value=httpx.Response(200, json={"uuid": "abc-123", "assertion": "Pathogenic"})
        )
        result = await client.erepo_interpretation(uuid="abc-123")
        assert result["assertion"] == "Pathogenic"

    @respx.mock
    async def test_interpretation_wrapped_data(self, client: ClingenClient) -> None:
        respx.get(f"{_EREPO}/api/classifications").mock(
            return_value=httpx.Response(200, json={"data": [{"caid": "CA9", "gene": "PAH"}]})
        )
        result = await client.erepo_interpretation(hgvs="NM_000277.3:c.1A>G")
        assert result["gene"] == "PAH"

    async def test_interpretation_requires_one_selector(self, client: ClingenClient) -> None:
        with pytest.raises(UpstreamInputError):
            await client.erepo_interpretation()
        with pytest.raises(UpstreamInputError):
            await client.erepo_interpretation(caid="CA1", uuid="x")

    @respx.mock
    async def test_interpretation_empty_is_not_found(self, client: ClingenClient) -> None:
        respx.get(f"{_EREPO}/api/classifications").mock(return_value=httpx.Response(200, json=[]))
        with pytest.raises(DataNotFoundError):
            await client.erepo_interpretation(caid="CA_MISSING")

    @respx.mock
    async def test_for_gene_live(self, client: ClingenClient) -> None:
        route = respx.get(f"{_EREPO}/api/classifications").mock(
            return_value=httpx.Response(200, json=[{"caid": "CA1"}, {"caid": "CA2"}])
        )
        rows = await client.erepo_for_gene_live("BRAF", match_limit=10)
        assert [r["caid"] for r in rows] == ["CA1", "CA2"]
        assert route.calls.last.request.url.params["gene"] == "BRAF"
        assert route.calls.last.request.url.params["matchLimit"] == "10"

    @respx.mock
    async def test_for_gene_live_variant_interpretations_wrapper(
        self, client: ClingenClient
    ) -> None:
        # The real live ?format=json endpoint wraps results under
        # variantInterpretations (drift caught by the integration tests).
        respx.get(f"{_EREPO}/api/classifications").mock(
            return_value=httpx.Response(
                200,
                json={
                    "@context": "https://erepo.genome.network/evrepo/api/context/light",
                    "variantInterpretations": [{"caid": "CA1"}, {"caid": "CA2"}],
                },
            )
        )
        rows = await client.erepo_for_gene_live("BRCA1")
        assert [r["caid"] for r in rows] == ["CA1", "CA2"]

    @respx.mock
    async def test_interpretation_variant_interpretations_wrapper(
        self, client: ClingenClient
    ) -> None:
        respx.get(f"{_EREPO}/api/classifications").mock(
            return_value=httpx.Response(
                200, json={"variantInterpretations": [{"caid": "CA9", "gene": "BRCA1"}]}
            )
        )
        result = await client.erepo_interpretation(caid="CA9")
        assert result["gene"] == "BRCA1"

    @respx.mock
    async def test_news(self, client: ClingenClient) -> None:
        respx.get(f"{_EREPO}/api/summary/news/").mock(
            return_value=httpx.Response(200, json={"data": [{"relatedVersion": "2.5.6"}]})
        )
        news = await client.erepo_news()
        assert news[0]["relatedVersion"] == "2.5.6"


class TestActionability:
    @respx.mock
    async def test_sepio(self, client: ClingenClient) -> None:
        route = respx.get(f"{_ACTION}/Adult/api/sepio/doc/AC1034").mock(
            return_value=httpx.Response(200, json={"docId": "AC1034", "@type": "SepioDoc"})
        )
        result = await client.actionability_sepio("AC1034", "Adult")
        assert result["docId"] == "AC1034"
        assert route.called


class TestResilience:
    @respx.mock
    async def test_retry_then_success(self, client: ClingenClient) -> None:
        route = respx.get(f"{_EREPO}/api/summary/news/").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json={"data": [{"relatedVersion": "2.5.6"}]}),
            ]
        )
        news = await client.erepo_news()
        assert news[0]["relatedVersion"] == "2.5.6"
        assert route.call_count == 2

    @respx.mock
    async def test_404_maps_to_not_found(self, client: ClingenClient) -> None:
        respx.get(f"{_ACTION}/Adult/api/sepio/doc/ACX").mock(return_value=httpx.Response(404))
        with pytest.raises(DataNotFoundError):
            await client.actionability_sepio("ACX", "Adult")

    @respx.mock
    async def test_400_maps_to_input_error(self, client: ClingenClient) -> None:
        respx.get(f"{_EREPO}/api/classifications").mock(return_value=httpx.Response(400))
        with pytest.raises(UpstreamInputError):
            await client.erepo_interpretation(caid="bad")

    @respx.mock
    async def test_persistent_429_maps_to_rate_limited(self, client: ClingenClient) -> None:
        respx.get(f"{_EREPO}/api/summary/news/").mock(return_value=httpx.Response(429))
        with pytest.raises(RateLimitedError):
            await client.erepo_news()

    @respx.mock
    async def test_500_exhausts_to_api_error(self, client: ClingenClient) -> None:
        respx.get(f"{_EREPO}/api/summary/news/").mock(return_value=httpx.Response(500))
        with pytest.raises(ClingenApiError):
            await client.erepo_news()

    @respx.mock
    async def test_transport_error_wraps(self, client: ClingenClient) -> None:
        respx.get(f"{_EREPO}/api/summary/news/").mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(ClingenApiError):
            await client.erepo_news()

    async def test_context_manager_closes(self) -> None:
        async with ClingenClient(erepo_base=_EREPO) as c:
            assert c is not None
