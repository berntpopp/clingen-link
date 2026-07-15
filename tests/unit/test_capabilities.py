"""Tests for finalized capabilities + resources + instructions (Task 4.7)."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

pytestmark = pytest.mark.asyncio


async def _call(mcp: FastMCP, name: str, args: dict[str, object]) -> dict[str, object]:
    result = await mcp.call_tool(name, args)
    return result.structured_content or {}


class TestCapabilities:
    async def test_lists_every_registered_tool(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_server_capabilities", {})
        assert payload["success"] is True
        registered = {t.name for t in await tool_mcp.list_tools()}
        listed = set(payload["tools"])
        assert registered == listed, registered ^ listed

    async def test_token_cost_hints_cover_tools(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_server_capabilities", {})
        assert set(payload["token_cost_hints"]) == set(payload["tools"])

    async def test_datasets_have_freshness(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_server_capabilities", {})
        ds = payload["datasets"]
        assert {"validity", "dosage", "actionability", "erepo"} <= set(ds)
        # Freshness folded in from the snapshot meta rows.
        assert ds["validity"]["version"]
        assert ds["validity"]["record_count"] is not None

    async def test_cspec_flows_into_rendered_capabilities(self, tool_mcp: FastMCP) -> None:
        """cspec must surface in the rendered payload, not just the private dict."""
        payload = await _call(tool_mcp, "get_server_capabilities", {})
        # Datasets section carries the cspec domain with its labelled name.
        datasets = payload["datasets"]
        assert "cspec" in datasets
        assert "Criteria Specification" in datasets["cspec"]["label"]
        # Tools list + token-cost section expose all four cspec tools.
        cspec_tools = {"list_cspecs", "get_cspec", "get_cspec_criterion", "search_cspec"}
        assert cspec_tools <= set(payload["tools"])
        assert cspec_tools <= set(payload["token_cost_hints"])

    async def test_error_codes_and_conventions(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_server_capabilities", {})
        assert "upstream_unavailable" in payload["error_codes"]
        assert "response_mode" in payload["parameter_conventions"]
        assert payload["resources"]["clingen://citations"]

    async def test_capabilities_version_stable(self, tool_mcp: FastMCP) -> None:
        first = await _call(tool_mcp, "get_server_capabilities", {})
        second = await _call(tool_mcp, "get_server_capabilities", {})
        assert first["capabilities_version"]
        assert first["capabilities_version"] == second["capabilities_version"]


class TestResources:
    async def test_all_resources_resolve(self, tool_mcp: FastMCP) -> None:
        uris = [
            "clingen://capabilities",
            "clingen://usage",
            "clingen://reference",
            "clingen://freshness",
            "clingen://research-use",
            "clingen://citations",
            "clingen://guidance",
        ]
        registered = {str(r.uri) for r in await tool_mcp.list_resources()}
        for uri in uris:
            assert uri in registered, uri

    async def test_citations_resource_has_framework_and_license(self, tool_mcp: FastMCP) -> None:
        result = await tool_mcp.read_resource("clingen://citations")
        import json

        payload = json.loads(result.contents[0].content)
        assert "28552198" in payload["framework_citation"]
        assert "CC BY 4.0" in payload["license"]

    async def test_freshness_resource_has_domains(self, tool_mcp: FastMCP) -> None:
        result = await tool_mcp.read_resource("clingen://freshness")
        import json

        payload = json.loads(result.contents[0].content)
        assert {"validity", "dosage", "actionability", "erepo"} <= set(payload["domains"])

    async def test_guidance_resource_resolves_and_has_baseline(self, tool_mcp: FastMCP) -> None:
        registered = {str(r.uri) for r in await tool_mcp.list_resources()}
        assert "clingen://guidance" in registered
        result = await tool_mcp.read_resource("clingen://guidance")
        import json

        payload = json.loads(result.contents[0].content)
        assert payload["baseline"]["gn_id"] == "GN001"
        assert payload["unsafe_for_clinical_use"] is True
        assert payload["research_use_notice"]
        assert all(e["oa_license"] for e in payload["recommendations"])
