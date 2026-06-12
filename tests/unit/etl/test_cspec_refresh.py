from clingen_link.etl import refresh


def test_cspec_in_domain_list() -> None:
    assert "cspec" in refresh._DOMAINS


def test_load_cspec_filters_and_parses(monkeypatch) -> None:
    from clingen_link.etl import build, cspec_fetch

    catalog = [
        {"entId": "GN164", "ld": {"CriteriaCode": 1, "RuleSet": 1}},
        {"entId": "GN199", "ld": {"CriteriaCode": 0, "RuleSet": 1}},  # candidate filtered out
    ]
    jsonld = {
        "@id": ".../id/GN164",
        "affiliation": {"@id": ".../id/50140", "label": "ABCA4"},
        "label": "x",
        "version": "1.0.0",
        "cspecStatus": "Released",
        "ruleSets": [
            {
                "@id": ".../id/777",
                "genes": [],
                "criteriaCodes": [{"@id": ".../id/1", "label": "BS3", "evidenceStrengths": []}],
            }
        ],
    }
    monkeypatch.setattr(cspec_fetch, "fetch_catalog", lambda c: catalog)
    monkeypatch.setattr(cspec_fetch, "fetch_spec_jsonld", lambda c, gn: jsonld)
    monkeypatch.setattr(cspec_fetch, "fetch_doc_page", lambda c, gn: "<html></html>")
    monkeypatch.setattr(cspec_fetch, "head_file", lambda c, u: {})

    sources = build.Sources()
    refresh._load_cspec(sources, client=None)
    assert sources.cspec_catalog == catalog
    assert len(sources.cspec_specs) == 1
    assert sources.cspec_specs[0].spec["gn_id"] == "GN164"


def test_load_cspec_check_path_is_catalog_only(monkeypatch) -> None:
    """``--check`` path (with_specs=False) fetches the catalog and nothing else."""
    from clingen_link.etl import build, cspec_fetch

    catalog = [{"entId": "GN164", "ld": {"CriteriaCode": 1, "RuleSet": 1}}]

    def _boom(*a, **k):
        raise AssertionError("per-spec fetch in check path")

    monkeypatch.setattr(cspec_fetch, "fetch_catalog", lambda c: catalog)
    monkeypatch.setattr(cspec_fetch, "fetch_spec_jsonld", _boom)
    monkeypatch.setattr(cspec_fetch, "fetch_doc_page", _boom)
    monkeypatch.setattr(cspec_fetch, "head_file", _boom)

    sources = build.Sources()
    refresh._load_cspec(sources, client=None, with_specs=False)
    assert sources.cspec_catalog == catalog
    assert sources.cspec_specs == []


def test_load_cspec_isolates_per_spec_failure(monkeypatch) -> None:
    """One spec's SourceFetchError drops only that spec, not the whole domain."""
    from clingen_link.etl import build, cspec_fetch
    from clingen_link.exceptions import SourceFetchError

    catalog = [
        {"entId": "GN1", "ld": {"CriteriaCode": 1, "RuleSet": 1}},
        {"entId": "GN2", "ld": {"CriteriaCode": 1, "RuleSet": 1}},
    ]
    jsonld_gn2 = {
        "@id": ".../id/GN2",
        "affiliation": {"@id": ".../id/50140", "label": "ABCA4"},
        "label": "x",
        "version": "1.0.0",
        "cspecStatus": "Released",
        "ruleSets": [
            {
                "@id": ".../id/777",
                "genes": [],
                "criteriaCodes": [{"@id": ".../id/1", "label": "BS3", "evidenceStrengths": []}],
            }
        ],
    }

    def _fetch_jsonld(c, gn):
        if gn == "GN1":
            raise SourceFetchError("boom", source="cspec")
        return jsonld_gn2

    monkeypatch.setattr(cspec_fetch, "fetch_catalog", lambda c: catalog)
    monkeypatch.setattr(cspec_fetch, "fetch_spec_jsonld", _fetch_jsonld)
    monkeypatch.setattr(cspec_fetch, "fetch_doc_page", lambda c, gn: "<html></html>")
    monkeypatch.setattr(cspec_fetch, "head_file", lambda c, u: {})

    sources = build.Sources()
    refresh._load_cspec(sources, client=None)
    assert len(sources.cspec_specs) == 1
    assert sources.cspec_specs[0].spec["gn_id"] == "GN2"


def test_load_cspec_keeps_spec_when_doc_page_fails(monkeypatch) -> None:
    """GN001 scenario: a doc-page SourceFetchError keeps the spec, drops attachments."""
    from clingen_link.etl import build, cspec_fetch
    from clingen_link.exceptions import SourceFetchError

    catalog = [{"entId": "GN001", "ld": {"CriteriaCode": 1, "RuleSet": 1}}]
    jsonld = {
        "@id": ".../id/GN001",
        "affiliation": {"@id": ".../id/50140", "label": "ABCA4"},
        "label": "x",
        "version": "1.0.0",
        "cspecStatus": "Released",
        "ruleSets": [
            {
                "@id": ".../id/777",
                "genes": [],
                "criteriaCodes": [{"@id": ".../id/1", "label": "BS3", "evidenceStrengths": []}],
            }
        ],
    }

    def _doc_boom(c, gn):
        raise SourceFetchError("doc 500", source="cspec")

    def _head_boom(*a, **k):
        raise AssertionError("head_file called when doc page failed (no urls)")

    monkeypatch.setattr(cspec_fetch, "fetch_catalog", lambda c: catalog)
    monkeypatch.setattr(cspec_fetch, "fetch_spec_jsonld", lambda c, gn: jsonld)
    monkeypatch.setattr(cspec_fetch, "fetch_doc_page", _doc_boom)
    monkeypatch.setattr(cspec_fetch, "head_file", _head_boom)

    sources = build.Sources()
    refresh._load_cspec(sources, client=None)
    assert len(sources.cspec_specs) == 1
    parsed = sources.cspec_specs[0]
    assert parsed.spec["gn_id"] == "GN001"
    # Criteria preserved from the JSON-LD that already succeeded.
    assert [c["code"] for c in parsed.criteria] == ["BS3"]
    # Doc page failed -> no attachments harvested.
    assert parsed.files == []


def test_load_cspec_keeps_spec_when_one_attachment_head_fails(monkeypatch) -> None:
    """One attachment HEAD failure omits only its metadata; both file links survive."""
    from clingen_link.etl import build, cspec_fetch
    from clingen_link.exceptions import SourceFetchError

    url_fail = "https://cspec.genome.network/cspec/File/id/aaa1/data"
    url_ok = "https://cspec.genome.network/cspec/File/id/bbb2/data"
    doc_html = '<a href="/cspec/File/id/aaa1/data">f1</a><a href="/cspec/File/id/bbb2/data">f2</a>'
    catalog = [{"entId": "GN500", "ld": {"CriteriaCode": 1, "RuleSet": 1}}]
    jsonld = {
        "@id": ".../id/GN500",
        "affiliation": {"@id": ".../id/50140", "label": "ABCA4"},
        "label": "x",
        "version": "1.0.0",
        "cspecStatus": "Released",
        "ruleSets": [
            {
                "@id": ".../id/777",
                "genes": [],
                "criteriaCodes": [{"@id": ".../id/1", "label": "BS3", "evidenceStrengths": []}],
            }
        ],
    }

    def _head(c, u):
        if u == url_fail:
            raise SourceFetchError("head 500", source="cspec")
        return {
            "content-disposition": "filename=ok.pdf",
            "content-type": "x",
            "content-length": "5",
        }

    monkeypatch.setattr(cspec_fetch, "fetch_catalog", lambda c: catalog)
    monkeypatch.setattr(cspec_fetch, "fetch_spec_jsonld", lambda c, gn: jsonld)
    monkeypatch.setattr(cspec_fetch, "fetch_doc_page", lambda c, gn: doc_html)
    monkeypatch.setattr(cspec_fetch, "head_file", _head)

    sources = build.Sources()
    refresh._load_cspec(sources, client=None)
    assert len(sources.cspec_specs) == 1
    parsed = sources.cspec_specs[0]
    assert parsed.spec["gn_id"] == "GN500"
    assert len(parsed.files) == 2
    by_url = {f["download_url"]: f for f in parsed.files}
    # The successful HEAD records the filename.
    assert by_url[url_ok]["filename"] == "ok.pdf"
    # The failed HEAD still records the file link (uuid + download_url) with null metadata.
    failed = by_url[url_fail]
    assert failed["filename"] is None
    assert failed["content_type"] is None
    assert failed["size_bytes"] is None
    assert failed["download_url"] == url_fail
    assert failed["file_uuid"] == "aaa1"


def test_load_cspec_skips_non_released_before_doc_fetch(monkeypatch) -> None:
    """A non-Released candidate is dropped before any doc-page fetch occurs."""
    from clingen_link.etl import build, cspec_fetch

    catalog = [{"entId": "GN300", "ld": {"CriteriaCode": 1, "RuleSet": 1}}]
    jsonld = {
        "@id": ".../id/GN300",
        "affiliation": {"@id": ".../id/50140", "label": "ABCA4"},
        "label": "x",
        "version": "1.0.0",
        "cspecStatus": "In Development",
        "ruleSets": [
            {
                "@id": ".../id/777",
                "genes": [],
                "criteriaCodes": [{"@id": ".../id/1", "label": "BS3", "evidenceStrengths": []}],
            }
        ],
    }

    def _boom(*a, **k):
        raise AssertionError("doc-page fetched for non-Released spec")

    monkeypatch.setattr(cspec_fetch, "fetch_catalog", lambda c: catalog)
    monkeypatch.setattr(cspec_fetch, "fetch_spec_jsonld", lambda c, gn: jsonld)
    monkeypatch.setattr(cspec_fetch, "fetch_doc_page", _boom)
    monkeypatch.setattr(cspec_fetch, "head_file", _boom)

    sources = build.Sources()
    refresh._load_cspec(sources, client=None)
    assert sources.cspec_specs == []
