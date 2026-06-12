"""Capabilities tool plus resource handlers for clingen-link."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from mcp.types import Annotations

from clingen_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from clingen_link.mcp.errors import run_mcp_tool
from clingen_link.mcp.resources import (
    get_capabilities_resource,
    get_research_use_resource,
)
from clingen_link.mcp.schema_relax import relax_output_schema
from clingen_link.mcp.service_adapters import ClingenServices

_RESOURCE_ANNOTATIONS = Annotations(audience=["assistant"], priority=1.0)

_CAPABILITIES_OUTPUT_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "server_version": {"type": "string"},
            "mcp_protocol_version": {"type": "string"},
            "research_use_only": {"type": "boolean"},
            "datasets": {"type": "object"},
            "tools": {"type": "array", "items": {"type": "string"}},
            "resources": {"type": "object"},
            "error_codes": {"type": "array", "items": {"type": "string"}},
            "research_use_notice": {"type": "string"},
            "_meta": {"type": "object"},
        },
    }
)


def register_metadata_tools(
    mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]
) -> None:
    @mcp.tool(
        name="get_server_capabilities",
        title="Get clingen-link Capabilities",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_CAPABILITIES_OUTPUT_SCHEMA,
        tags={"metadata"},
    )
    async def get_server_capabilities() -> dict[str, Any]:
        """Use this when a client needs the supported tools, ClinGen datasets, recommended workflows, error taxonomy, or current limitations. Returns ~2kB."""

        async def call() -> dict[str, Any]:
            return get_capabilities_resource()

        return await run_mcp_tool("get_server_capabilities", call)

    @mcp.resource(
        "clingen://capabilities",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def capabilities_resource() -> dict[str, Any]:
        return get_capabilities_resource()

    @mcp.resource(
        "clingen://research-use",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def research_use_resource() -> dict[str, Any]:
        return get_research_use_resource()
