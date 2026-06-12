# tests/unit/etl/test_cspec_fetch.py
import httpx
import respx

from clingen_link.etl import cspec_fetch

_BASE = "https://cspec.genome.network"


@respx.mock
def test_fetch_catalog_returns_data_rows() -> None:
    respx.get(f"{_BASE}/cspec/SequenceVariantInterpretation/id").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"entId": "GN001", "ld": {"CriteriaCode": 28, "RuleSet": 1}}],
                "status": {"code": 200},
            },
        )
    )
    with httpx.Client() as client:
        rows = cspec_fetch.fetch_catalog(client)
    assert rows[0]["entId"] == "GN001"


@respx.mock
def test_fetch_spec_jsonld_and_doc_page() -> None:
    respx.get(f"{_BASE}/cspec/api/SequenceVariantInterpretation/id/GN164").mock(
        return_value=httpx.Response(200, json={"@id": ".../id/GN164", "version": "1.0.0"})
    )
    respx.get(f"{_BASE}/cspec/ui/svi/doc/GN164").mock(
        return_value=httpx.Response(200, text="<html>doc</html>")
    )
    with httpx.Client() as client:
        assert cspec_fetch.fetch_spec_jsonld(client, "GN164")["version"] == "1.0.0"
        assert "doc" in cspec_fetch.fetch_doc_page(client, "GN164")


@respx.mock
def test_head_file_returns_lowercased_headers() -> None:
    url = f"{_BASE}/cspec/File/id/abc/data"
    respx.head(url).mock(
        return_value=httpx.Response(
            200, headers={"Content-Type": "application/pdf", "Content-Length": "10"}
        )
    )
    with httpx.Client() as client:
        headers = cspec_fetch.head_file(client, url)
    assert headers["content-type"] == "application/pdf"
    assert headers["content-length"] == "10"
