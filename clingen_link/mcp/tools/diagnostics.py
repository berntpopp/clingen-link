"""Diagnostics tool for the clingen-link MCP server."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP

from clingen_link.mcp.annotations import READ_ONLY_CLOSED_WORLD
from clingen_link.mcp.errors import get_recent_errors, get_recent_schema_drift, run_mcp_tool
from clingen_link.mcp.resources import MCP_PROTOCOL_VERSION, _server_version
from clingen_link.mcp.schema_relax import relax_output_schema
from clingen_link.mcp.service_adapters import ClingenServices


def register_diagnostics_tools(
    mcp: FastMCP,
    *,
    service_factory: Callable[[], ClingenServices],
) -> None:
    @mcp.tool(
        name="get_clingen_diagnostics",
        title="Get clingen-link Diagnostics",
        annotations=READ_ONLY_CLOSED_WORLD,
        tags={"metadata", "diagnostics"},
        output_schema=relax_output_schema(
            {
                "type": "object",
                "properties": {
                    "server_version": {"type": "string"},
                    "mcp_protocol_version": {"type": "string"},
                    "recent_errors": {"type": "array", "items": {"type": "object"}},
                    "recent_error_count": {"type": "integer"},
                    "recent_schema_drift": {"type": "array", "items": {"type": "object"}},
                    "recent_schema_drift_count": {"type": "integer"},
                    "snapshot": {"type": "object"},
                    "_meta": {"type": "object"},
                },
                "required": [
                    "server_version",
                    "mcp_protocol_version",
                    "recent_errors",
                    "recent_error_count",
                ],
            }
        ),
    )
    async def get_clingen_diagnostics() -> dict[str, Any]:
        """Use this when an LLM hits repeated errors or needs server health information; returns recent error history, server version, snapshot freshness, and recent_schema_drift entries so an LLM that hit output_validation_failed can self-diagnose. Returns <1kB."""

        async def call() -> dict[str, Any]:
            recent = get_recent_errors()
            drift = get_recent_schema_drift()
            return {
                "server_version": _server_version(),
                "mcp_protocol_version": MCP_PROTOCOL_VERSION,
                "recent_errors": recent,
                "recent_error_count": len(recent),
                "recent_schema_drift": drift,
                "recent_schema_drift_count": len(drift),
                # Snapshot freshness is a placeholder until the store lands in a
                # later phase; surfaced now so the contract is stable.
                "snapshot": {
                    "status": "not_loaded",
                    "detail": "Snapshot store is not wired in yet (Phase 1 scaffold).",
                },
                "_meta": {
                    "next_commands": [{"tool": "get_server_capabilities", "arguments": {}}],
                    "unsafe_for_clinical_use": True,
                },
            }

        return await run_mcp_tool("get_clingen_diagnostics", call)
