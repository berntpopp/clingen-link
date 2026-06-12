"""Reference tool: list_expert_panels (GCEP/VCEP affiliates + curation counts)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from clingen_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from clingen_link.mcp.envelope import build_meta, cross_domain_version
from clingen_link.mcp.errors import McpErrorContext, run_mcp_tool
from clingen_link.mcp.next_commands import cmd
from clingen_link.mcp.schema_relax import relax_output_schema
from clingen_link.mcp.service_adapters import ClingenServices

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]

_PANELS_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "expert_panels": {"type": "array", "items": {"type": "object"}},
            "total": {"type": "integer"},
            "_meta": {"type": "object"},
        },
    }
)


def register_reference_tools(
    mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]
) -> None:
    """Register list_expert_panels on ``mcp``."""

    @mcp.tool(
        name="list_expert_panels",
        title="List ClinGen Expert Panels",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_PANELS_SCHEMA,
        tags={"reference"},
    )
    async def list_expert_panels(
        query: Annotated[
            str | None,
            Field(
                description="Filter by expert-panel label text (FTS).",
                examples=["cardiomyopathy", "RASopathy"],
            ),
        ] = None,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) is the same compact list; full is identical."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this to list ClinGen GCEP/VCEP expert panels (affiliates) with their curation counts, optionally filtered by label text. Useful to resolve an expert-panel name before filtering validity or variant interpretations. Returns ~1-5kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            panels = services.gene.expert_panels(query=query, limit=100)
            records = [p.model_dump() for p in panels]
            headline = (
                f"{len(records)} ClinGen expert panel(s)"
                + (f" matching '{query}'" if query else "")
                + "."
            )
            next_commands = (
                [cmd("search_validity", expert_panel=panels[0].label or panels[0].affiliate_id)]
                if panels
                else [cmd("get_server_capabilities")]
            )
            return {
                "headline": headline,
                "expert_panels": records,
                "total": len(records),
                "_meta": build_meta(
                    data_version=cross_domain_version(services.meta()),
                    next_commands=next_commands,
                    record_count=len(records),
                ),
            }

        return await run_mcp_tool(
            "list_expert_panels",
            call,
            context=McpErrorContext(tool_name="list_expert_panels", query=query),
        )
