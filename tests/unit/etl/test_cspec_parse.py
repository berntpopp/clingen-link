# tests/unit/etl/test_cspec_parse.py
from clingen_link.etl import cspec_parse

_JSONLD = {
    "@id": "https://cspec.genome.network/cspec/api/SequenceVariantInterpretation/id/GN164",
    "@type": "Criteria Specification",
    "affiliation": {
        "@id": "https://cspec.genome.network/cspec/api/Organization/id/50140",
        "label": "ABCA4 Variant Curation Expert Panel ",
        "url": "https://clinicalgenome.org/affiliation/50140",
    },
    "label": "ClinGen ABCA4 Expert Panel Specifications ... Version 1.0.0",
    "version": "1.0.0",
    "cspecStatus": "Released",
    "currentStatus": "Pilot Rules In Prep",
    "lastUpdated": "2024-02-06T00:00:00.000Z",
    "ruleSets": [
        {
            "@id": "https://cspec.genome.network/cspec/api/RuleSet/id/777",
            "genes": [
                {
                    "@id": "https://www.genenames.org/tools/search/#!/?query=ABCA4",
                    "diseases": [
                        {
                            "@id": "http://purl.obolibrary.org/obo/MONDO_0800406",
                            "label": "MONDO:0800406",
                        }
                    ],
                    "modeOfInheritance": "Autosomal recessive",
                }
            ],
            "criteriaCodes": [
                {
                    "@id": "https://cspec.genome.network/cspec/api/CriteriaCode/id/538211541",
                    "label": "BS3",
                    "description": "Well-established functional studies show no damaging effect.",
                    "evidenceStrengths": [
                        {
                            "label": "Supporting",
                            "applicability": "Applicable",
                            "description": "See PS3/BS3 spreadsheet below.",
                        },
                        {"label": "Strong", "applicability": "Not Applicable"},
                    ],
                },
            ],
        }
    ],
}

_DOC_HTML = """
<h3>BS3</h3>
<p>guidance</p>
<a href="/cspec/File/id/abc-123/data">spreadsheet</a>
<a href="https://cspec.genome.network/cspec/File/id/def-456/data">general</a>
"""
_HEADS = {
    "https://cspec.genome.network/cspec/File/id/abc-123/data": {
        "content-disposition": "attachment; filename=PS3-BS3-list.xlsx",
        "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content-length": "13962",
    },
    "https://cspec.genome.network/cspec/File/id/def-456/data": {
        "content-disposition": "attachment; filename=General.pdf",
        "content-type": "application/octet-stream",
        "content-length": "100",
    },
}


def test_is_published_uses_cspec_status_not_current_status() -> None:
    assert cspec_parse.is_published(_JSONLD) is True  # Released + 1 criterion
    deleted = {**_JSONLD, "cspecStatus": "CSpec Deleted", "ruleSets": []}
    assert cspec_parse.is_published(deleted) is False
    baseline = {**_JSONLD, "cspecStatus": None, "@id": ".../id/GN001"}
    assert cspec_parse.is_published(baseline) is True  # GN001 baseline allowlisted


def test_parse_spec_structures_rows() -> None:
    parsed = cspec_parse.parse_spec(_JSONLD, _DOC_HTML, _HEADS)
    assert parsed.spec["gn_id"] == "GN164"
    assert parsed.spec["affiliation_id"] == "50140"
    assert parsed.spec["affiliation_label"] == "ABCA4 Variant Curation Expert Panel"
    assert parsed.spec["cspec_status"] == "Released"
    assert [rs["rule_set_id"] for rs in parsed.rule_sets] == ["777"]
    assert parsed.genes[0]["gene_symbol"] == "ABCA4"
    assert parsed.genes[0]["mondo"] == "MONDO:0800406"
    assert parsed.criteria[0]["criteria_id"] == "538211541"
    assert parsed.criteria[0]["code"] == "BS3"
    strengths = {s["strength_label"]: s["applicability"] for s in parsed.strengths}
    assert strengths == {"Supporting": "Applicable", "Strong": "Not Applicable"}
    files = {f["filename"]: f for f in parsed.files}
    assert files["PS3-BS3-list.xlsx"]["size_bytes"] == 13962
    # File under the BS3 heading associates to that criterion; the trailing one is spec-level.
    assert files["PS3-BS3-list.xlsx"]["criteria_id"] == "538211541"
    assert files["General.pdf"]["criteria_id"] in (None, "538211541")


def _criteria_code(criteria_id: str, label: str) -> dict[str, object]:
    """Minimal criteriaCode entry (only the keys parse_spec reads)."""
    return {
        "@id": f"https://cspec.genome.network/cspec/api/CriteriaCode/id/{criteria_id}",
        "label": label,
    }


def _rule_set(rule_set_id: str, criteria_codes: list[dict[str, object]]) -> dict[str, object]:
    """Minimal ruleSet entry (only the keys parse_spec reads)."""
    return {
        "@id": f"https://cspec.genome.network/cspec/api/RuleSet/id/{rule_set_id}",
        "criteriaCodes": criteria_codes,
    }


def _spec_jsonld(rule_sets: list[dict[str, object]]) -> dict[str, object]:
    """Minimal spec JSON-LD wrapping the given rule sets."""
    return {
        "@id": "https://cspec.genome.network/cspec/api/SequenceVariantInterpretation/id/GN164",
        "ruleSets": rule_sets,
    }


def test_multi_rule_set_ambiguity_collapses_to_spec_level() -> None:
    # Two rule sets each define "PM2" with DIFFERENT criteria_ids -> ambiguous,
    # so any file under a PM2 heading must collapse to spec-level (None).
    jsonld = _spec_jsonld(
        [
            _rule_set("777", [_criteria_code("111", "PM2")]),
            _rule_set("888", [_criteria_code("222", "PM2")]),
        ]
    )
    # NB: file id must be hex (_FILE_RE assumes hex UUIDs); a non-hex id like
    # "xyz-1" would silently not match, so we use a hex id to exercise the
    # ambiguity collapse rather than the drop path.
    doc_html = '<h3>PM2</h3><a href="/cspec/File/id/abc-1/data">x</a>'
    parsed = cspec_parse.parse_spec(jsonld, doc_html, {})

    assert len(parsed.files) == 1
    assert parsed.files[0]["criteria_id"] is None

    pm2 = [c for c in parsed.criteria if c["code"] == "PM2"]
    assert len(pm2) == 2
    assert {c["criteria_id"] for c in pm2} == {"111", "222"}


def test_extract_file_urls_dedups_and_normalizes() -> None:
    # Order: relative aaa, absolute bbb, duplicate aaa. Expect absolute URLs,
    # de-duplicated, in first-occurrence order.
    doc_html = (
        '<a href="/cspec/File/id/aaa/data">1</a>'
        '<a href="https://cspec.genome.network/cspec/File/id/bbb/data">2</a>'
        '<a href="/cspec/File/id/aaa/data">3</a>'
    )
    assert cspec_parse.extract_file_urls(doc_html) == [
        "https://cspec.genome.network/cspec/File/id/aaa/data",
        "https://cspec.genome.network/cspec/File/id/bbb/data",
    ]


def test_prose_token_redirects_attribution_known_limitation() -> None:
    # Single rule set with distinct PS3 and BS3 criteria_ids. The file sits under
    # the <h3>PS3</h3> heading, but a prose mention of "BS3" precedes it. The
    # nearest preceding code token wins, so the file attaches to BS3, NOT PS3.
    # This PINS the documented Task-12 association limitation -- it is the known
    # trade-off, not desired behavior. Do not change the algorithm to "fix" it.
    jsonld = _spec_jsonld(
        [
            _rule_set(
                "777",
                [
                    _criteria_code("301", "PS3"),
                    _criteria_code("302", "BS3"),
                ],
            )
        ]
    )
    doc_html = (
        '<h3>PS3</h3><p>note: differs from BS3 below</p><a href="/cspec/File/id/f1/data">x</a>'
    )
    parsed = cspec_parse.parse_spec(jsonld, doc_html, {})

    assert len(parsed.files) == 1
    assert parsed.files[0]["criteria_id"] == "302"  # BS3, the nearest preceding code token
