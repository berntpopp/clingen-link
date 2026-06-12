from clingen_link.models.models import CspecDetail, CspecSummary, CriteriaCode


def test_cspec_summary_citation() -> None:
    row = {
        "gn_id": "GN092",
        "affiliation_id": "50087",
        "affiliation_label": "ENIGMA BRCA1 and BRCA2 VCEP",
        "label": "ENIGMA spec",
        "version": "1.1.0",
        "cspec_status": "Released",
        "current_status": "Released",
        "last_updated": "2024-08-09T00:00:00.000Z",
        "permalink": "https://cspec.genome.network/cspec/ui/svi/doc/GN092",
    }
    m = CspecSummary.from_row(row)
    assert m.gn_id == "GN092"
    assert "ENIGMA BRCA1 and BRCA2 VCEP" in m.recommended_citation
    assert m.permalink.endswith("/doc/GN092")


def test_criteria_code_model() -> None:
    c = CriteriaCode.from_row(
        {
            "criteria_id": "1",
            "gn_id": "GN092",
            "code": "PVS1",
            "description": "null variant",
            "strengths": [
                {
                    "strength_label": "Very Strong",
                    "applicability": "Applicable",
                    "description": None,
                }
            ],
            "files": [],
        }
    )
    assert c.code == "PVS1" and c.strengths[0].strength_label == "Very Strong"
