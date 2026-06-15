"""Tests for the gene-disease validity tools (Task 4.3)."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

pytestmark = pytest.mark.asyncio


async def _call(mcp: FastMCP, name: str, args: dict[str, object]) -> dict[str, object]:
    result = await mcp.call_tool(name, args)
    return result.structured_content or {}


class TestGetGeneValidity:
    async def test_lists_assertions(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_validity", {"gene_symbol": "AARS1"})
        assert payload["success"] is True
        assert payload["total"] == 1
        rec = payload["records"][0]
        assert rec["classification"] == "Definitive"
        assert rec["recommended_citation"].startswith("ClinGen Gene-Disease Validity")
        assert rec["permalink"].startswith("https://search.clinicalgenome.org/kb/gene-validity/")
        # M4: citation lives per-record, not duplicated into _meta.
        assert "recommended_citation" not in payload["_meta"]

    async def test_classification_filter(self, tool_mcp: FastMCP) -> None:
        payload = await _call(
            tool_mcp, "get_gene_validity", {"gene_symbol": "AARS1", "classification": "Refuted"}
        )
        assert payload["success"] is True
        assert payload["total"] == 0
        assert payload["records"] == []

    async def test_moi_filter_matches(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_validity", {"gene_symbol": "AARS1", "moi": "AD"})
        assert payload["total"] == 1

    async def test_unknown_gene_not_found(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_validity", {"gene_symbol": "ZZZNOPE"})
        assert payload["success"] is False
        assert payload["error_code"] == "not_found"
        assert payload["fallback_tool"] == "search_genes"

    async def test_next_commands_non_empty_args(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_validity", {"gene_symbol": "AARS1"})
        cmds = payload["_meta"]["next_commands"]
        assert cmds
        for c in cmds:
            assert c["tool"]
            assert c["arguments"]


class TestSearchValidity:
    async def test_disease_text_search(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_validity", {"disease": "Charcot"})
        assert payload["success"] is True
        assert payload["total"] == 1
        assert payload["records"][0]["symbol"] == "AARS1"

    async def test_pagination_truncated_block(self, tool_mcp: FastMCP) -> None:
        # size=1 over the 5-row fixture forces a truncated block.
        payload = await _call(tool_mcp, "search_validity", {"size": 1})
        assert payload["page"] == 1
        assert payload["size"] == 1
        assert payload["total"] >= 2
        trunc = payload["_meta"]["truncated"]
        assert trunc["kind"] == "pagination"
        assert trunc["dropped"] >= 1
        assert trunc["to_restore"] == "page=2"

    async def test_classification_filter(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_validity", {"classification": "Definitive"})
        assert payload["success"] is True
        assert all(r["classification"] == "Definitive" for r in payload["records"])

    async def test_gene_filter(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_validity", {"gene_symbol": "AARS1"})
        assert payload["total"] == 1
        assert payload["records"][0]["symbol"] == "AARS1"
