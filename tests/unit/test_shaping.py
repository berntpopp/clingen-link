"""Tests for the response shapers + truncated block builder (Task 4.1)."""

from __future__ import annotations

from clingen_link.mcp.shaping import shape_record, shape_records, truncated_block
from clingen_link.models.models import DosageRecord, ValidityAssertion


def _validity() -> ValidityAssertion:
    return ValidityAssertion.from_row(
        {
            "symbol": "BRCA1",
            "hgnc_id": "HGNC:1100",
            "disease_name": "breast-ovarian cancer",
            "mondo": "MONDO:0003582",
            "moi": "AD",
            "classification": "Definitive",
            "expert_panel": "Hereditary Breast/Ovarian Cancer GCEP",
            "sop": "SOP10",
            "perm_id": "CGGV:assertion_x",
            "report_id": "rep-1",
            "affiliate_id": "40042",
            "classified_date": "2024-01-01",
            "released": "01/01/2024",
        }
    )


def _dosage() -> DosageRecord:
    return DosageRecord.from_row(
        {
            "record_type": "gene",
            "symbol": "BRCA1",
            "hgnc_id": "HGNC:1100",
            "isca_id": None,
            "cytoband": "17q21.31",
            "grch37": None,
            "grch38": "chr17:43044295-43125483",
            "haplo_score": "3",
            "haplo_description": "Sufficient evidence",
            "haplo_mondo": "MONDO:0003582",
            "haplo_pmids": ["123", "456"],
            "triplo_score": "0",
            "triplo_description": "No evidence",
            "triplo_mondo": None,
            "triplo_pmids": [],
            "date_last_evaluated": "2024-01-01",
        }
    )


class TestShapeRecord:
    def test_full_keeps_everything(self) -> None:
        out = shape_record(_validity(), domain="validity", response_mode="full")
        assert out["sop"] == "SOP10"
        assert out["expert_panel"] == "Hereditary Breast/Ovarian Cancer GCEP"
        assert out["recommended_citation"]

    def test_compact_drops_verbose_and_nulls(self) -> None:
        out = shape_record(_dosage(), domain="dosage", response_mode="compact")
        # Verbose fields dropped.
        assert "haplo_pmids" not in out
        assert "haplo_description" not in out
        # Null/empty fields dropped.
        assert "isca_id" not in out
        assert "grch37" not in out
        assert "triplo_pmids" not in out
        # Kept content + citation contract preserved.
        assert out["haplo_score"] == "3"
        assert out["recommended_citation"]
        assert out["permalink"]

    def test_standard_keeps_nulls_drops_verbose(self) -> None:
        out = shape_record(_dosage(), domain="dosage", response_mode="standard")
        assert "isca_id" in out  # nulls kept
        assert out["isca_id"] is None
        assert "haplo_pmids" not in out  # verbose still dropped

    def test_citation_never_stripped_in_compact(self) -> None:
        out = shape_record(_validity(), domain="validity", response_mode="compact")
        assert out["recommended_citation"].startswith("ClinGen Gene-Disease Validity")
        assert out["permalink"].startswith("https://search.clinicalgenome.org")


class TestShapeRecords:
    def test_minimal_returns_empty_list(self) -> None:
        assert shape_records([_validity()], domain="validity", response_mode="minimal") == []

    def test_compact_list(self) -> None:
        out = shape_records([_validity(), _validity()], domain="validity", response_mode="compact")
        assert len(out) == 2
        assert all("recommended_citation" in r for r in out)


class TestTruncatedBlock:
    def test_shape(self) -> None:
        block = truncated_block(
            kind="pagination",
            dropped=42,
            to_disable="size",
            to_restore="page=2",
            filter_applied={"gene": "BRCA1"},
        )
        assert block == {
            "kind": "pagination",
            "dropped": 42,
            "to_disable": "size",
            "to_restore": "page=2",
            "filter": {"gene": "BRCA1"},
        }

    def test_minimal_args(self) -> None:
        block = truncated_block(kind="summary_cap", dropped=3)
        assert block["kind"] == "summary_cap"
        assert block["dropped"] == 3
        assert block["filter"] == {}
        assert "to_disable" not in block
