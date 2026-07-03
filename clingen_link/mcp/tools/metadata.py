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
    get_citations_resource,
    get_freshness_resource,
    get_guidance_resource,
    get_reference_resource,
    get_research_use_resource,
    get_usage_resource,
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
            "token_cost_hints": {"type": "object"},
            "resources": {"type": "object"},
            "error_codes": {"type": "array", "items": {"type": "string"}},
            "parameter_conventions": {"type": "object"},
            "capabilities_version": {"type": "string"},
            "research_use_notice": {"type": "string"},
            "_meta": {"type": "object"},
        },
    }
)


def _safe_meta(service_factory: Callable[[], ClingenServices]) -> dict[str, Any] | None:
    """Best-effort snapshot freshness; None when the snapshot is unavailable.

    A missing snapshot must not break capabilities discovery, so any failure to
    build the services or read meta degrades to a freshness-less document.
    """
    try:
        return service_factory().meta()
    except Exception:
        return None


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
        """Use this when a client needs the supported tools, ClinGen datasets + per-domain snapshot freshness, recommended workflows, token-cost hints, error taxonomy, parameter conventions, or the capabilities_version content hash. Returns ~4kB."""

        async def call() -> dict[str, Any]:
            return get_capabilities_resource(_safe_meta(service_factory))

        return await run_mcp_tool("get_server_capabilities", call)

    @mcp.resource(
        "clingen://capabilities",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def capabilities_resource() -> dict[str, Any]:
        return get_capabilities_resource(_safe_meta(service_factory))

    @mcp.resource("clingen://usage", annotations=_RESOURCE_ANNOTATIONS)
    def usage_resource() -> str:
        return get_usage_resource()

    @mcp.resource(
        "clingen://reference",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def reference_resource() -> dict[str, Any]:
        return get_reference_resource()

    @mcp.resource(
        "clingen://freshness",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def freshness_resource() -> dict[str, Any]:
        return get_freshness_resource(_safe_meta(service_factory))

    @mcp.resource(
        "clingen://research-use",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def research_use_resource() -> dict[str, Any]:
        return get_research_use_resource()

    @mcp.resource(
        "clingen://citations",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def citations_resource() -> dict[str, Any]:
        return get_citations_resource()

    @mcp.resource(
        "clingen://guidance",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def guidance_resource() -> dict[str, Any]:
        return get_guidance_resource()
