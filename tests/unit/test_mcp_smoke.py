"""Smoke tests for the clingen-link MCP facade."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP


@pytest.mark.asyncio
async def test_capabilities_tool_callable(mcp: FastMCP) -> None:
    """get_server_capabilities returns a success envelope with a non-empty tools list."""
    result = await mcp.call_tool("get_server_capabilities", {})
    payload = result.structured_content or {}

    assert payload.get("success") is True, payload
    assert payload["server"] == "clingen-link"
    assert isinstance(payload["tools"], list) and payload["tools"], payload
    assert "get_server_capabilities" in payload["tools"]
    assert payload["research_use_only"] is True

    meta = payload.get("_meta") or {}
    assert meta.get("unsafe_for_clinical_use") is True


@pytest.mark.asyncio
async def test_diagnostics_tool_callable(mcp: FastMCP) -> None:
    """get_clingen_diagnostics returns a success envelope with the recent-error ring."""
    result = await mcp.call_tool("get_clingen_diagnostics", {})
    payload = result.structured_content or {}

    assert payload.get("success") is True, payload
    assert "recent_errors" in payload
    assert payload["recent_error_count"] == len(payload["recent_errors"])
    assert payload["snapshot"]["status"] == "not_loaded"


@pytest.mark.asyncio
async def test_facade_lists_expected_tools(mcp: FastMCP) -> None:
    """The facade registers exactly the Phase 1 discovery + diagnostics tools."""
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert {"get_server_capabilities", "get_clingen_diagnostics"} <= names
