"""Tests for the gene dosage tools (Task 4.4)."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

pytestmark = pytest.mark.asyncio


async def _call(mcp: FastMCP, name: str, args: dict[str, object]) -> dict[str, object]:
    result = await mcp.call_tool(name, args)
    return result.structured_content or {}


class TestGetGeneDosage:
    async def test_haplo_triplo_interpretation(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_dosage", {"gene_symbol": "AAGAB"})
        assert payload["success"] is True
        rec = payload["results"][0]
        assert rec["record_type"] == "gene"
        assert rec["haplo_score"] == "3"
        assert rec["haplo_interpretation"] == "Sufficient evidence for dosage pathogenicity"
        assert "triplo_interpretation" in rec
        assert rec["recommended_citation"].startswith("ClinGen Dosage Sensitivity")
        assert "haploinsufficiency" in payload["headline"]
        assert "recommended_citation" not in payload["_meta"]

    async def test_resolvable_gene_no_dosage_is_success_zero(self, tool_mcp: FastMCP) -> None:
        # M5: ABCA3 resolves (validity fixture) but has no dosage record → success+0, not not_found.
        payload = await _call(tool_mcp, "get_gene_dosage", {"gene_symbol": "ABCA3"})
        assert payload["success"] is True
        assert payload["total"] == 0
        assert payload["results"] == []

    async def test_unknown_gene_not_found(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_dosage", {"gene_symbol": "ZZZNOPE"})
        assert payload["success"] is False
        assert payload["error_code"] == "not_found"
        assert payload["fallback_tool"] == "get_server_capabilities"


class TestSearchDosage:
    async def test_record_type_filter(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_dosage", {"record_type": "region", "size": 100})
        assert payload["success"] is True
        assert payload["total"] >= 1
        assert all(r["record_type"] == "region" for r in payload["results"])

    async def test_haplo_score_filter(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_dosage", {"haplo_score": "3", "size": 100})
        assert all(r["haplo_score"] == "3" for r in payload["results"])

    async def test_pagination_truncated(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_dosage", {"size": 1})
        assert payload["page"] == 1
        if payload["total"] > 1:
            trunc = payload["_meta"]["truncated"]
            assert trunc["kind"] == "pagination"
            assert trunc["dropped"] >= 1

    async def test_next_commands_non_empty(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_dosage", {"size": 5})
        for c in payload["_meta"]["next_commands"]:
            assert c["tool"]
            assert c["arguments"]


class TestSearchDosageQueryForms:
    """Issue #157: ``query`` never answers an input it cannot match with an empty success."""

    async def test_cytoband_in_query_finds_the_region(self, tool_mcp: FastMCP) -> None:
        by_filter = await _call(tool_mcp, "search_dosage", {"cytoband": "15q13"})
        by_query = await _call(tool_mcp, "search_dosage", {"query": "15q13"})
        assert by_query["success"] is True
        assert by_query["total"] == by_filter["total"] >= 1
        assert {r["isca_id"] for r in by_query["results"]} >= {"ISCA-46753"}

    @pytest.mark.parametrize("query", ["Williams syndrome", "Williams", "zzznosuchterm"])
    async def test_unindexed_text_is_invalid_input_not_empty(
        self, tool_mcp: FastMCP, query: str
    ) -> None:
        payload = await _call(tool_mcp, "search_dosage", {"query": query})
        assert payload["success"] is False
        assert payload["error_code"] == "invalid_input"
        assert payload["field"] == "query"
        assert "mondo_search_diseases" in payload["recovery"]
        assert query not in str(payload)  # free text is never echoed

    async def test_curated_gene_by_hgnc_id_is_found(self, tool_mcp: FastMCP) -> None:
        # The index holds the symbol only; an HGNC id / alias must resolve, not read as absent.
        payload = await _call(tool_mcp, "search_dosage", {"query": "HGNC:20"})
        assert payload["success"] is True
        assert payload["total"] >= 1
        assert payload["_meta"]["next_commands"][0]["arguments"] == {"gene_symbol": "AARS1"}

    async def test_known_gene_without_dosage_is_honest_empty(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_dosage", {"query": "ABCA3"})
        assert payload["success"] is True
        assert payload["total"] == 0

    async def test_a_direct_hit_is_not_rerouted_through_the_alias_resolver(
        self, tool_mcp: FastMCP, tool_services, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The resolver ranks a casefolded alias over a symbol (real data: "ar" -> FDXR), so it
        # may only rescue a zero-hit query, never replace one that already matched.
        monkeypatch.setattr(tool_services.store, "resolve_gene", lambda _q: "AARS1")
        payload = await _call(tool_mcp, "search_dosage", {"query": "aagab"})
        assert payload["_meta"]["next_commands"][0]["arguments"] == {"gene_symbol": "AAGAB"}
