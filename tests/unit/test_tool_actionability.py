"""Tests for the clinical actionability tools (Task 4.5)."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastmcp import FastMCP

from tests.conftest import ACTION_TEST_BASE

pytestmark = pytest.mark.asyncio


async def _call(mcp: FastMCP, name: str, args: dict[str, object]) -> dict[str, object]:
    result = await mcp.call_tool(name, args)
    return result.structured_content or {}


class TestGetGeneActionability:
    async def test_snapshot_only(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_actionability", {"gene": "SCN1A"})
        assert payload["success"] is True
        assert payload["context"] == "Adult"
        rec = payload["records"][0]
        assert rec["doc_id"] == "AC1034"
        assert rec["recommended_citation"].startswith("ClinGen Clinical Actionability")
        assert "sepio_detail" not in rec
        assert "recommended_citation" not in payload["_meta"]

    async def test_pediatric_context_citation(self, tool_mcp: FastMCP) -> None:
        payload = await _call(
            tool_mcp, "get_gene_actionability", {"gene": "SCN1A", "context": "Pediatric"}
        )
        assert payload["context"] == "Pediatric"
        assert "Pediatric:" in payload["records"][0]["recommended_citation"]

    @respx.mock
    async def test_include_detail_live(self, tool_mcp: FastMCP) -> None:
        route = respx.get(f"{ACTION_TEST_BASE}/Adult/api/sepio/doc/AC1034").mock(
            return_value=httpx.Response(200, json={"docId": "AC1034", "@type": "SepioDoc"})
        )
        payload = await _call(
            tool_mcp, "get_gene_actionability", {"gene": "SCN1A", "include_detail": True}
        )
        assert payload["success"] is True
        assert payload["records"][0]["sepio_detail"]["docId"] == "AC1034"
        assert route.called

    async def test_resolvable_gene_no_curation_is_success_zero(self, tool_mcp: FastMCP) -> None:
        # M5: AARS1 resolves but has no actionability curation → success+0, not not_found.
        payload = await _call(tool_mcp, "get_gene_actionability", {"gene": "AARS1"})
        assert payload["success"] is True
        assert payload["total"] == 0
        assert payload["records"] == []

    async def test_unknown_gene_not_found(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_actionability", {"gene": "ZZZNOPE"})
        assert payload["success"] is False
        assert payload["fallback_tool"] == "search_genes"


class TestSearchActionability:
    async def test_disease_search(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_actionability", {"disease": "melanoma"})
        assert payload["success"] is True
        assert payload["total"] == 1
        assert payload["records"][0]["doc_id"] == "AC1060"

    async def test_gene_search(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_actionability", {"gene": "SCN1A"})
        assert payload["total"] >= 1
        assert any("SCN1A" in r["genes"] for r in payload["records"])

    async def test_next_commands_non_empty(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_actionability", {"gene": "SCN1A"})
        for c in payload["_meta"]["next_commands"]:
            assert c["tool"] and c["arguments"]
