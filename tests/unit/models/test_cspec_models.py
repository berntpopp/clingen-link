from clingen_link.models.models import CriteriaCode, CspecDetail, CspecSummary


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


def test_cspec_detail_assemble_round_trip() -> None:
    spec_row = {
        "gn_id": "GN092",
        "affiliation_id": "50087",
        "affiliation_label": "ENIGMA BRCA1 and BRCA2 VCEP",
        "label": "ENIGMA spec",
        "version": "1.1.0",
        "cspec_status": "Released",
        "current_status": "Released",
        "last_updated": "2024-08-09T00:00:00.000Z",
    }
    genes = [
        {
            "gene_symbol": "BRCA1",
            "hgnc_id": None,
            "mondo": "MONDO:0700268",
            "moi": "AD",
            "rule_set_id": "9",
            "gn_id": "GN092",
        }
    ]
    criteria = [
        {
            "criteria_id": "55",
            "rule_set_id": "9",
            "gn_id": "GN092",
            "code": "PVS1",
            "description": "null",
            "ord": 0,
        }
    ]
    files = [
        {
            "file_uuid": "abc",
            "gn_id": "GN092",
            "criteria_id": None,
            "filename": "guide.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
            "download_url": "https://x/abc/data",
        }
    ]
    d = CspecDetail.assemble(spec_row, genes=genes, criteria=criteria, files=files)
    assert d.genes[0].gene_symbol == "BRCA1"
    assert d.criteria[0].code == "PVS1"
    assert d.criteria[0].strengths == []
    assert d.criteria[0].files == []
    assert d.files[0].filename == "guide.pdf"
    assert d.recommended_citation
    assert "ENIGMA BRCA1 and BRCA2 VCEP" in d.recommended_citation
    assert d.permalink.endswith("/doc/GN092")


def test_criteria_code_from_bare_row_has_empty_lists() -> None:
    c = CriteriaCode.from_row(
        {
            "criteria_id": "55",
            "gn_id": "GN092",
            "code": "PVS1",
            "description": "null",
            "ord": 0,
        }
    )
    assert c.strengths == []
    assert c.files == []
    assert c.code == "PVS1"


def test_cspec_citation_permalink_fallback() -> None:
    m = CspecSummary.from_row(
        {
            "gn_id": "GN1",
            "affiliation_label": "X VCEP",
            "label": "spec",
            "version": "1.0.0",
        }
    )
    assert m.permalink == "https://cspec.genome.network/cspec/ui/svi/doc/GN1"
    assert "GN1" in m.recommended_citation
