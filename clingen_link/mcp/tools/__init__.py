"""Tool registration entry points for the clingen-link MCP facade."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from clingen_link.mcp.service_adapters import ClingenServices
from clingen_link.mcp.tools.diagnostics import register_diagnostics_tools
from clingen_link.mcp.tools.genes import register_gene_tools
from clingen_link.mcp.tools.metadata import register_metadata_tools


def register_clingen_tools(
    mcp: FastMCP,
    *,
    service_factory: Callable[[], ClingenServices],
) -> None:
    """Register all clingen-link MCP tools on the given server."""
    register_metadata_tools(mcp, service_factory=service_factory)
    register_gene_tools(mcp, service_factory=service_factory)
    register_diagnostics_tools(mcp, service_factory=service_factory)
