"""Tests for the per-domain store query functions (incl. FTS + pagination)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from clingen_link.store import queries as q
from clingen_link.store.db import Store
from clingen_link.store.search import fts_match, paginate


@pytest.fixture
def conn(store: Store) -> Iterator[object]:
    """A borrowed read-only connection from the small test store."""
    with store.connection() as c:
        yield c


class TestValidity:
    def test_for_gene(self, conn: object) -> None:
        rows = q.validity_for_gene(conn, "AARS1")  # type: ignore[arg-type]
        assert [r["disease_name"] for r in rows] == ["Charcot-Marie-Tooth disease axonal type 2N"]
        assert rows[0]["classification"] == "Definitive"

    def test_for_gene_classification_filter(self, conn: object) -> None:
        assert q.validity_for_gene(conn, "AARS1", classification="Refuted") == []  # type: ignore[arg-type]
        assert q.validity_for_gene(conn, "AARS1", classification="Definitive")  # type: ignore[arg-type]

    def test_search_by_disease_fts(self, conn: object) -> None:
        rows, total = q.search_validity(conn, text="Charcot")  # type: ignore[arg-type]
        assert total == 1
        assert rows[0]["symbol"] == "AARS1"

    def test_search_by_mondo(self, conn: object) -> None:
        rows, total = q.search_validity(conn, mondo="MONDO:0013212")  # type: ignore[arg-type]
        assert total == 1
        assert rows[0]["symbol"] == "AARS1"

    def test_search_no_match(self, conn: object) -> None:
        rows, total = q.search_validity(conn, text="zzznotadisease")  # type: ignore[arg-type]
        assert rows == []
        assert total == 0


class TestDosage:
    def test_for_gene_decodes_pmids(self, conn: object) -> None:
        rows = q.dosage_for_gene(conn, "AAGAB")  # type: ignore[arg-type]
        assert rows
        assert isinstance(rows[0]["haplo_pmids"], list)
        assert "23064416" in rows[0]["haplo_pmids"]

    def test_search_record_type_region(self, conn: object) -> None:
        rows, total = q.search_dosage(conn, record_type="region", size=100)  # type: ignore[arg-type]
        assert total > 0
        assert all(r["record_type"] == "region" for r in rows)

    def test_search_pagination_truncates(self, conn: object) -> None:
        rows, total = q.search_dosage(conn, record_type="gene", page=1, size=2)  # type: ignore[arg-type]
        assert len(rows) == 2
        assert total >= 2  # total exceeds page size → caller flags truncation


class TestActionability:
    def test_for_gene(self, conn: object) -> None:
        rows = q.actionability_for_gene(conn, "SCN1A")  # type: ignore[arg-type]
        assert [r["doc_id"] for r in rows] == ["AC1034"]
        assert isinstance(rows[0]["genes"], list)
        assert "SCN1A" in rows[0]["genes"]

    def test_for_gene_in_panel(self, conn: object) -> None:
        # LMNA is one of many genes on the dilated-cardiomyopathy curation.
        rows = q.actionability_for_gene(conn, "LMNA")  # type: ignore[arg-type]
        assert rows
        assert rows[0]["doc_id"] == "AC138"

    def test_search_disease_fts(self, conn: object) -> None:
        rows, total = q.search_actionability(conn, text="melanoma")  # type: ignore[arg-type]
        assert total == 1
        assert rows[0]["doc_id"] == "AC1060"


class TestErepo:
    def test_for_gene_paginated(self, conn: object) -> None:
        rows, total = q.erepo_for_gene(conn, "BRAF")  # type: ignore[arg-type]
        assert total == 1
        assert rows[0]["caid"] == "CA281951"
        assert isinstance(rows[0]["hgvs"], list)

    def test_by_caid(self, conn: object) -> None:
        row = q.erepo_by_caid(conn, "CA281951")  # type: ignore[arg-type]
        assert row is not None
        assert row["gene"] == "BRAF"

    def test_by_caid_missing(self, conn: object) -> None:
        assert q.erepo_by_caid(conn, "CA000000") is None  # type: ignore[arg-type]

    def test_by_hgvs(self, conn: object) -> None:
        row = q.erepo_by_hgvs(conn, "NM_004333.4:c.740T>C")  # type: ignore[arg-type]
        assert row is not None
        assert row["caid"] == "CA281951"

    def test_search_by_gene(self, conn: object) -> None:
        rows, total = q.search_erepo(conn, gene="GJB2")  # type: ignore[arg-type]
        assert total >= 1
        assert all(r["gene"] == "GJB2" for r in rows)


class TestGeneHubAndReference:
    def test_gene_summary_counts(self, conn: object) -> None:
        summary = q.gene_summary_counts(conn, "AARS1")  # type: ignore[arg-type]
        assert summary is not None
        assert summary["has_validity"] == 1
        assert summary["validity_count"] == 1
        assert summary["dosage_count"] == 1

    def test_gene_summary_missing(self, conn: object) -> None:
        assert q.gene_summary_counts(conn, "ZZZNOPE") is None  # type: ignore[arg-type]

    def test_search_genes_prefix(self, conn: object) -> None:
        rows = q.search_genes(conn, "AAR")  # type: ignore[arg-type]
        assert {r["symbol"] for r in rows} >= {"AARS1", "AARS2"}

    def test_search_genes_by_alias(self, conn: object) -> None:
        rows = q.search_genes(conn, "HGNC:20")  # type: ignore[arg-type]
        assert any(r["symbol"] == "AARS1" for r in rows)

    def test_expert_panels_ordered(self, conn: object) -> None:
        panels = q.expert_panels(conn, limit=3)  # type: ignore[arg-type]
        assert len(panels) == 3
        counts = [p["total_curations"] for p in panels]
        assert counts == sorted(counts, reverse=True)

    def test_expert_panels_query(self, conn: object) -> None:
        panels = q.expert_panels(conn, query="Mitochondrial")  # type: ignore[arg-type]
        assert panels
        assert "Mitochondrial" in panels[0]["label"]


class TestSearchHelpers:
    def test_fts_match_quotes_tokens(self) -> None:
        assert fts_match("BRCA1 cancer") == '"BRCA1" AND "cancer"'

    def test_fts_match_blank(self) -> None:
        assert fts_match("   ") is None
        assert fts_match("()") is None

    def test_fts_match_column_filter(self) -> None:
        assert fts_match('gene:"BRCA1"') == 'gene:"BRCA1"'

    def test_paginate_clamps(self) -> None:
        assert paginate(0, 10).page == 1
        assert paginate(2, 10).offset == 10
        assert paginate(1, 9999).size == 100
        assert paginate(1, 0).size == 25
