"""Tests for the domain services: models, citations, caching, live fallback."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from clingen_link.api.clingen_client import ClingenClient
from clingen_link.services.aggregator import ClingenServices
from clingen_link.store.db import Store

_EREPO = "https://erepo.test/evrepo"
_ACTION = "https://actionability.test/ac"


@pytest.fixture
async def services(store: Store) -> AsyncIterator[ClingenServices]:
    """A ClingenServices over the small test store + a test-pointed client."""
    client = ClingenClient(
        erepo_base=_EREPO,
        actionability_base=_ACTION,
        timeout_s=1.0,
        queue_wait_timeout_s=1.0,
    )
    svc = ClingenServices(store, client=client)
    try:
        yield svc
    finally:
        await svc.client.aclose()


class TestValidityService:
    async def test_for_gene_returns_models(self, services: ClingenServices) -> None:
        rows = await services.validity.for_gene("AARS1")
        assert rows
        assert rows[0].symbol == "AARS1"
        assert rows[0].classification == "Definitive"

    async def test_citation_format(self, services: ClingenServices) -> None:
        row = (await services.validity.for_gene("AARS1"))[0]
        assert row.recommended_citation.startswith("ClinGen Gene-Disease Validity: AARS1 —")
        assert "Charcot-Marie-Tooth disease axonal type 2N" in row.recommended_citation
        assert "(MONDO:0013212)" in row.recommended_citation
        assert "Definitive (AD)" in row.recommended_citation
        assert row.permalink.startswith(
            "https://search.clinicalgenome.org/kb/gene-validity/CGGV:assertion_"
        )
        assert row.permalink in row.recommended_citation

    async def test_classification_filter(self, services: ClingenServices) -> None:
        assert await services.validity.for_gene("AARS1", classification="Refuted") == []

    async def test_caching_returns_equal(self, services: ClingenServices) -> None:
        first = await services.validity.for_gene("AARS1")
        second = await services.validity.for_gene("AARS1")
        assert [r.perm_id for r in first] == [r.perm_id for r in second]

    async def test_search(self, services: ClingenServices) -> None:
        rows, total = await services.validity.search(text="Charcot")
        assert total == 1
        assert rows[0].symbol == "AARS1"


class TestDosageService:
    async def test_for_gene(self, services: ClingenServices) -> None:
        rows = await services.dosage.for_gene("AAGAB")
        assert rows
        assert rows[0].record_type == "gene"
        assert isinstance(rows[0].haplo_pmids, list)

    async def test_search(self, services: ClingenServices) -> None:
        rows, total = await services.dosage.search(record_type="region", size=100)
        assert total >= 1
        assert all(r.record_type == "region" for r in rows)

    async def test_citation(self, services: ClingenServices) -> None:
        row = (await services.dosage.for_gene("AAGAB"))[0]
        assert row.recommended_citation.startswith("ClinGen Dosage Sensitivity: AAGAB —")
        assert "haploinsufficiency:" in row.recommended_citation
        assert "triplosensitivity:" in row.recommended_citation
        assert row.permalink.startswith("https://search.clinicalgenome.org/kb/gene-dosage/")


class TestActionabilityService:
    async def test_for_gene(self, services: ClingenServices) -> None:
        rows = await services.actionability.for_gene("SCN1A")
        assert rows
        assert rows[0].doc_id == "AC1034"
        assert "SCN1A" in rows[0].genes

    async def test_citation(self, services: ClingenServices) -> None:
        row = (await services.actionability.for_gene("SCN1A"))[0]
        assert row.recommended_citation.startswith("ClinGen Clinical Actionability:")
        assert "(AC1034)" in row.recommended_citation
        assert "Adult:" in row.recommended_citation
        assert row.recommended_citation.endswith("https://actionability.clinicalgenome.org/ac/")

    async def test_search(self, services: ClingenServices) -> None:
        rows, total = await services.actionability.search(text="melanoma")
        assert total == 1
        assert rows[0].doc_id == "AC1060"

    async def test_pediatric_context_citation(self, services: ClingenServices) -> None:
        rows = await services.actionability.for_gene("SCN1A", context="Pediatric")
        assert "Pediatric:" in rows[0].recommended_citation

    @respx.mock
    async def test_sepio_detail_live(self, services: ClingenServices) -> None:
        route = respx.get(f"{_ACTION}/Adult/api/sepio/doc/AC1034").mock(
            return_value=httpx.Response(200, json={"docId": "AC1034", "@type": "SepioDoc"})
        )
        detail = await services.actionability.sepio_detail("AC1034", "Adult")
        assert detail["docId"] == "AC1034"
        assert route.called


class TestErepoService:
    async def test_for_gene_snapshot(self, services: ClingenServices) -> None:
        rows, total = await services.erepo.for_gene("BRAF")
        assert total == 1
        assert rows[0].caid == "CA281951"
        assert rows[0].assertion == "Likely Pathogenic"

    async def test_citation(self, services: ClingenServices) -> None:
        rows, _ = await services.erepo.for_gene("BRAF")
        cite = rows[0].recommended_citation
        assert cite.startswith("ClinGen Variant Pathogenicity (ERepo):")
        assert "(CA281951)" in cite
        assert "Likely Pathogenic by RASopathy VCEP" in cite

    async def test_search(self, services: ClingenServices) -> None:
        rows, total = await services.erepo.search(gene="BRAF")
        assert total == 1
        assert rows[0].gene == "BRAF"

    async def test_get_interpretation_prefers_snapshot(self, services: ClingenServices) -> None:
        # No respx mock — must be served from the snapshot without any live call.
        result = await services.erepo.get_interpretation(caid="CA281951")
        assert result.gene == "BRAF"

    async def test_get_interpretation_by_hgvs_snapshot(self, services: ClingenServices) -> None:
        result = await services.erepo.get_interpretation(hgvs="NM_004333.4:c.740T>C")
        assert result.caid == "CA281951"

    @respx.mock
    async def test_get_interpretation_live_fallback(self, services: ClingenServices) -> None:
        # CAID absent from the snapshot → live fallback (news version probe first).
        respx.get(f"{_EREPO}/api/summary/news/").mock(
            return_value=httpx.Response(200, json={"data": [{"relatedVersion": "2.5.6"}]})
        )
        route = respx.get(f"{_EREPO}/api/classifications").mock(
            return_value=httpx.Response(
                200, json=[{"caid": "CA999999", "gene": "TP53", "assertion": "Pathogenic"}]
            )
        )
        result = await services.erepo.get_interpretation(caid="CA999999")
        assert result.gene == "TP53"
        assert route.called

    @respx.mock
    async def test_get_interpretation_refresh_forces_live(self, services: ClingenServices) -> None:
        respx.get(f"{_EREPO}/api/summary/news/").mock(
            return_value=httpx.Response(200, json={"data": [{"relatedVersion": "2.5.6"}]})
        )
        route = respx.get(f"{_EREPO}/api/classifications").mock(
            return_value=httpx.Response(
                200, json=[{"caid": "CA281951", "gene": "BRAF", "assertion": "Pathogenic"}]
            )
        )
        # CA281951 IS in the snapshot, but refresh=True must bypass it and call live.
        result = await services.erepo.get_interpretation(caid="CA281951", refresh=True)
        assert result.assertion == "Pathogenic"
        assert route.called


class TestGeneService:
    def test_resolve(self, services: ClingenServices) -> None:
        assert services.gene.resolve("hgnc:20") == "AARS1"

    def test_search(self, services: ClingenServices) -> None:
        rows = services.gene.search("AAR")
        assert {r["symbol"] for r in rows} >= {"AARS1", "AARS2"}

    def test_expert_panels(self, services: ClingenServices) -> None:
        panels = services.gene.expert_panels(limit=3)
        assert len(panels) == 3
        assert panels[0].total_curations >= panels[-1].total_curations

    async def test_get_summary_aggregates_domains(self, services: ClingenServices) -> None:
        summary = await services.gene.get_summary("AARS1")
        assert summary is not None
        assert summary.symbol == "AARS1"
        assert summary.has_validity is True
        assert summary.validity_count == 1
        assert summary.dosage_count == 1
        assert len(summary.validity) == 1
        assert "ClinGen gene summary for AARS1" in summary.recommended_citation

    async def test_get_summary_missing(self, services: ClingenServices) -> None:
        assert await services.gene.get_summary("ZZZNOPE") is None


class TestAggregator:
    def test_meta(self, services: ClingenServices) -> None:
        meta = services.meta()
        assert set(meta) == {"validity", "dosage", "actionability", "erepo"}

    async def test_from_snapshot_missing_raises(self, tmp_path: object) -> None:
        from clingen_link.exceptions import SnapshotUnavailableError

        with pytest.raises(SnapshotUnavailableError):
            ClingenServices.from_snapshot(f"{tmp_path}/nope.sqlite")  # type: ignore[arg-type]
