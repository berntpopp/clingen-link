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
