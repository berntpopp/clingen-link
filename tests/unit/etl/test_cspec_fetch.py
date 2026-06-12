# tests/unit/etl/test_cspec_fetch.py
import httpx
import pytest
import respx

from clingen_link.etl import cspec_fetch
from clingen_link.exceptions import SourceFetchError

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
    # The File endpoint rejects HEAD (400) but serves GET, so head_file uses a
    # streaming GET; mock the GET route accordingly.
    url = f"{_BASE}/cspec/File/id/abc/data"
    respx.get(url).mock(
        return_value=httpx.Response(
            200, headers={"Content-Type": "application/pdf", "Content-Length": "10"}
        )
    )
    with httpx.Client() as client:
        headers = cspec_fetch.head_file(client, url)
    assert headers["content-type"] == "application/pdf"
    assert headers["content-length"] == "10"


@respx.mock
def test_head_file_streams_headers_without_consuming_body() -> None:
    # head_file must read only the response headers and never download the body
    # (attachment files can be multiple MB). A large body must not be required.
    url = f"{_BASE}/cspec/File/id/big/data"
    route = respx.get(url).mock(
        return_value=httpx.Response(
            200,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": "attachment; filename=spec.pdf",
                "Content-Length": "5242880",
            },
            content=b"x" * 5_242_880,
        )
    )
    with httpx.Client() as client:
        headers = cspec_fetch.head_file(client, url)
    assert route.called
    assert headers["content-type"] == "application/octet-stream"
    assert headers["content-disposition"] == "attachment; filename=spec.pdf"
    assert headers["content-length"] == "5242880"


@respx.mock
def test_head_file_400_raises_source_fetch_error() -> None:
    # A GET that returns >=400 (the real registry returns 400 to HEAD) must raise.
    url = f"{_BASE}/cspec/File/id/bad/data"
    respx.get(url).mock(return_value=httpx.Response(400, json={"error": "bad request"}))
    with httpx.Client() as client, pytest.raises(SourceFetchError):
        cspec_fetch.head_file(client, url)


@respx.mock
def test_fetch_catalog_paginates_until_short_page() -> None:
    def _catalog(request: httpx.Request) -> httpx.Response:
        pg = request.url.params["pg"]
        if pg == "1":
            return httpx.Response(200, json={"data": [{"entId": "GN001"}, {"entId": "GN002"}]})
        return httpx.Response(200, json={"data": [{"entId": "GN003"}]})

    route = respx.get(f"{_BASE}/cspec/SequenceVariantInterpretation/id").mock(side_effect=_catalog)
    with httpx.Client() as client:
        rows = cspec_fetch.fetch_catalog(client, page_size=2)
    assert [r["entId"] for r in rows] == ["GN001", "GN002", "GN003"]
    # Two requests must have been issued: a full page 1 and a short page 2.
    assert route.call_count == 2
    assert route.calls[0].request.url.params["pg"] == "1"
    assert route.calls[1].request.url.params["pg"] == "2"


@respx.mock
def test_fetch_catalog_bad_shape_raises() -> None:
    respx.get(f"{_BASE}/cspec/SequenceVariantInterpretation/id").mock(
        return_value=httpx.Response(200, json={"status": {"code": 200}})  # no "data" list
    )
    with httpx.Client() as client, pytest.raises(SourceFetchError):
        cspec_fetch.fetch_catalog(client)


@respx.mock
def test_head_file_404_raises_source_fetch_error() -> None:
    url = f"{_BASE}/cspec/File/id/missing/data"
    respx.get(url).mock(return_value=httpx.Response(404))
    with httpx.Client() as client, pytest.raises(SourceFetchError):
        cspec_fetch.head_file(client, url)


@respx.mock
def test_fetch_catalog_page_size_zero_terminates() -> None:
    # page_size is clamped to >= 1; a single short page (0 rows < 1) must stop the loop.
    respx.get(f"{_BASE}/cspec/SequenceVariantInterpretation/id").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    with httpx.Client() as client:
        rows = cspec_fetch.fetch_catalog(client, page_size=0)
    assert rows == []
