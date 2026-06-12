"""Tests for the gene hub tools: search_genes + get_gene_summary (Task 4.2)."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

pytestmark = pytest.mark.asyncio


async def _call(mcp: FastMCP, name: str, args: dict[str, object]) -> dict[str, object]:
    result = await mcp.call_tool(name, args)
    return result.structured_content or {}


class TestSearchGenes:
    async def test_resolves_and_lists_domains(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_genes", {"query": "AARS1"})
        assert payload["success"] is True
        assert payload["resolved_symbol"] == "AARS1"
        assert payload["candidates"]
        cand = payload["candidates"][0]
        assert {"has_validity", "has_dosage", "has_actionability", "erepo_variant_count"} <= set(
            cand
        )
        # next_commands must reference a real tool with non-empty args.
        nxt = payload["_meta"]["next_commands"]
        assert nxt[0]["tool"] == "get_gene_summary"
        assert nxt[0]["arguments"] == {"gene": "AARS1"}

    async def test_alias_resolution(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_genes", {"query": "HGNC:20"})
        assert payload["resolved_symbol"] == "AARS1"

    async def test_data_version_present(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_genes", {"query": "AARS1"})
        dv = payload["_meta"]["data_version"]
        assert "validity" in dv and dv["validity"]["version"]

    async def test_unknown_returns_not_found(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "search_genes", {"query": "ZZZNOPE"})
        assert payload["success"] is False
        assert payload["error_code"] == "not_found"
        assert payload["fallback_tool"] == "search_genes"
        assert payload["_meta"]["next_commands"]


class TestGeneSummary:
    async def test_returns_all_sections(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_summary", {"gene": "AARS1"})
        assert payload["success"] is True
        assert payload["symbol"] == "AARS1"
        assert payload["counts"]["validity"] == 1
        assert isinstance(payload["validity"], list) and payload["validity"]
        assert payload["validity"][0]["recommended_citation"].startswith("ClinGen")
        # recommended_citation surfaced on the summary + in _meta.
        assert "ClinGen gene summary for AARS1" in payload["recommended_citation"]
        assert payload["_meta"]["recommended_citation"] == payload["recommended_citation"]

    async def test_next_commands_into_domains(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_summary", {"gene": "AARS1"})
        tools = {c["tool"] for c in payload["_meta"]["next_commands"]}
        assert {
            "get_gene_validity",
            "get_gene_dosage",
            "get_gene_actionability",
            "get_variant_interpretations",
        } <= tools
        for c in payload["_meta"]["next_commands"]:
            assert c["arguments"] == {"gene": "AARS1"}

    async def test_minimal_drops_lists(self, tool_mcp: FastMCP) -> None:
        payload = await _call(
            tool_mcp, "get_gene_summary", {"gene": "AARS1", "response_mode": "minimal"}
        )
        assert payload["success"] is True
        assert "validity" not in payload
        assert payload["counts"]["validity"] == 1

    async def test_unknown_gene_not_found(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_gene_summary", {"gene": "ZZZNOPE"})
        assert payload["success"] is False
        assert payload["error_code"] == "not_found"
        assert payload["fallback_tool"] == "search_genes"
