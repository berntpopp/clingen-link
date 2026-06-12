"""Tests for the reference tool: list_expert_panels (Task 4.6)."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

pytestmark = pytest.mark.asyncio


async def _call(mcp: FastMCP, name: str, args: dict[str, object]) -> dict[str, object]:
    result = await mcp.call_tool(name, args)
    return result.structured_content or {}


class TestListExpertPanels:
    async def test_lists_panels(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "list_expert_panels", {})
        assert payload["success"] is True
        assert payload["total"] >= 1
        panel = payload["expert_panels"][0]
        assert "affiliate_id" in panel
        assert "total_curations" in panel
        # Sorted by curation count, descending.
        counts = [p["total_curations"] for p in payload["expert_panels"]]
        assert counts == sorted(counts, reverse=True)

    async def test_query_filter(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "list_expert_panels", {"query": "Cancer"})
        assert payload["success"] is True
        assert all("cancer" in (p["label"] or "").lower() for p in payload["expert_panels"])

    async def test_next_commands_non_empty(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "list_expert_panels", {})
        for c in payload["_meta"]["next_commands"]:
            assert c["tool"] and c["arguments"]
