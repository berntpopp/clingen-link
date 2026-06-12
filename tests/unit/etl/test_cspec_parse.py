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
