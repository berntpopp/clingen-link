from clingen_link.etl import refresh


def test_cspec_in_domain_list() -> None:
    assert "cspec" in refresh._DOMAINS


def test_load_cspec_filters_and_parses(monkeypatch) -> None:
    from clingen_link.etl import build, cspec_fetch, cspec_parse

    catalog = [
        {"entId": "GN164", "ld": {"CriteriaCode": 1, "RuleSet": 1}},
        {"entId": "GN199", "ld": {"CriteriaCode": 0, "RuleSet": 1}},  # candidate filtered out
    ]
    jsonld = {
        "@id": ".../id/GN164", "affiliation": {"@id": ".../id/50140", "label": "ABCA4"},
        "label": "x", "version": "1.0.0", "cspecStatus": "Released",
        "ruleSets": [{"@id": ".../id/777", "genes": [],
                      "criteriaCodes": [{"@id": ".../id/1", "label": "BS3",
                                         "evidenceStrengths": []}]}],
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
