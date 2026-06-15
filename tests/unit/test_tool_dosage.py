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
        rec = payload["records"][0]
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
        assert payload["records"] == []

    async def test_unknown_gene_not_found(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_dosage", {"gene_symbol": "ZZZNOPE"})
        assert payload["success"] is False
        assert payload["error_code"] == "not_found"
        assert payload["fallback_tool"] == "search_genes"


class TestSearchDosage:
    async def test_record_type_filter(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_dosage", {"record_type": "region", "size": 100})
        assert payload["success"] is True
        assert payload["total"] >= 1
        assert all(r["record_type"] == "region" for r in payload["records"])

    async def test_haplo_score_filter(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_dosage", {"haplo_score": "3", "size": 100})
        assert all(r["haplo_score"] == "3" for r in payload["records"])

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
