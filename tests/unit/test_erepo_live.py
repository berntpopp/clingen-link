"""Tests for the live ERepo payload adapter (assessment H1)."""

from __future__ import annotations

from clingen_link.models.models import VariantInterpretation
from clingen_link.services.erepo_live import erepo_live_to_row


def test_classifications_summary_maps_to_row() -> None:
    summary = {
        "caid": "CA003681",
        "variationId": "12345",
        "hgvs": ["NC_000017.11:g.43045761A>C", "NM_007294.4:c.5509T>G"],
        "@id": "https://erepo.clinicalgenome.org/evrepo/ui/interpretation/abc",
        "uuid": "abc",
        "gene": {"label": "BRCA1", "NCBI_id": "672"},
        "condition": {
            "label": "hereditary breast cancer",
            "@id": "http://purl.obolibrary.org/MONDO:0007254",
        },
        "publishedDate": "2021-01-01",
    }
    row = erepo_live_to_row(summary)
    # The H1 crash was feeding a {label,...} dict where a str is expected.
    assert row["gene"] == "BRCA1"
    assert row["caid"] == "CA003681"
    assert row["clinvar_variation_id"] == "12345"
    assert row["published_date"] == "2021-01-01"
    assert row["mondo"] == "MONDO:0007254"
    assert row["hgvs"] == summary["hgvs"]
    # The adapted row must build a model without raising (the regression we are fixing).
    model = VariantInterpretation.from_row(row)
    assert model.gene == "BRCA1"


def test_sepio_enrichment_adds_evidence_codes() -> None:
    summary = {"caid": "CA1", "uuid": "u", "gene": {"label": "BRCA1"}, "hgvs": []}
    sepio = {
        "statementOutcome": {"label": "Pathogenic"},
        "summary": "Meets PM2, PP3.",
        "evidenceLine": [
            {"evidenceCriterion": {"label": "PM2"}, "criterionMet": True},
            {"evidenceCriterion": {"label": "PP3"}, "criterionMet": True},
            {"evidenceCriterion": {"label": "BS1"}, "criterionMet": False},
        ],
    }
    row = erepo_live_to_row(summary, sepio=sepio)
    assert row["assertion"] == "Pathogenic"
    assert set(row["evidence_codes_met"]) == {"PM2", "PP3"}
    assert row["evidence_codes_not_met"] == ["BS1"]
    assert row["summary"] == "Meets PM2, PP3."


def test_adapter_is_lenient_with_sparse_payload() -> None:
    row = erepo_live_to_row({"caid": "CA9"})
    assert row["caid"] == "CA9"
    assert row["gene"] is None
    assert row["hgvs"] == []
    # Still constructs a model.
    assert VariantInterpretation.from_row(row).caid == "CA9"
