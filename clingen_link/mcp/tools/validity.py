"""Gene-disease validity tools: get_gene_validity + search_validity (snapshot).

Every record carries a CGGV permalink + verbatim ``recommended_citation`` (the
citation contract). ``search_validity`` paginates and emits a ``truncated`` block
when more matches exist than the requested page.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from clingen_link.exceptions import DataNotFoundError
from clingen_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from clingen_link.mcp.envelope import build_meta, data_version_for
from clingen_link.mcp.errors import McpErrorContext, run_mcp_tool
from clingen_link.mcp.next_commands import cmd
from clingen_link.mcp.patterns import GENE_SYMBOL_PATTERN, MONDO_PATTERN
from clingen_link.mcp.schema_relax import relax_output_schema
from clingen_link.mcp.service_adapters import ClingenServices
from clingen_link.mcp.shaping import shape_records, truncated_block

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]
_CLASSIFICATION = Literal[
    "Definitive",
    "Strong",
    "Moderate",
    "Limited",
    "Disputed",
    "Refuted",
    "No Known Disease Relationship",
]
_MOI = Literal["AD", "AR", "XL", "MT", "SD", "Undetermined"]

_LIST_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "records": {"type": "array", "items": {"type": "object"}},
            "total": {"type": "integer"},
            "page": {"type": "integer"},
            "size": {"type": "integer"},
            "recommended_citation": {"type": ["string", "null"]},
            "_meta": {"type": "object"},
        },
    }
)


def register_validity_tools(
    mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]
) -> None:
    """Register get_gene_validity + search_validity on ``mcp``."""

    @mcp.tool(
        name="get_gene_validity",
        title="Get Gene-Disease Validity",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_LIST_SCHEMA,
        tags={"validity"},
    )
    async def get_gene_validity(
        gene: Annotated[
            str,
            Field(
                description="Gene symbol or HGNC id (resolve with search_genes first).",
                min_length=1,
                max_length=64,
                pattern=GENE_SYMBOL_PATTERN + r"|^HGNC:\d+$",
                examples=["BRCA1", "AARS1"],
            ),
        ],
        classification: Annotated[
            _CLASSIFICATION | None,
            Field(
                description="Filter to one ClinGen validity classification.",
                examples=["Definitive"],
            ),
        ] = None,
        moi: Annotated[
            _MOI | None,
            Field(description="Filter to one mode of inheritance.", examples=["AD"]),
        ] = None,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims verbose fields; full keeps everything."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this to list ClinGen gene-disease validity assertions (Definitive…Refuted) for one gene, optionally filtered by classification or mode of inheritance. Each record carries a CGGV permalink + recommended_citation. Returns ~1-6kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            symbol = services.gene.resolve(gene)
            if symbol is None:
                raise DataNotFoundError(
                    f"Gene '{gene}' is not in the ClinGen snapshot. Resolve with search_genes."
                )
            models = await services.validity.for_gene(
                symbol, classification=classification, moi=moi
            )
            records = shape_records(models, domain="validity", response_mode=response_mode)
            citation = models[0].recommended_citation if models else None
            headline = (
                f"{symbol}: {len(models)} validity assertion(s)"
                + (f" classified {classification}" if classification else "")
                + "."
            )
            return {
                "headline": headline,
                "records": records,
                "total": len(models),
                "recommended_citation": citation,
                "_meta": build_meta(
                    data_version=data_version_for(services.meta(), "validity"),
                    next_commands=[
                        cmd("get_gene_summary", gene=symbol),
                        cmd("get_variant_interpretations", gene=symbol),
                    ],
                    record_count=len(models),
                ),
            }

        return await run_mcp_tool(
            "get_gene_validity",
            call,
            context=McpErrorContext(tool_name="get_gene_validity", gene=gene),
        )

    @mcp.tool(
        name="search_validity",
        title="Search Gene-Disease Validity",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_LIST_SCHEMA,
        tags={"validity"},
    )
    async def search_validity(
        disease: Annotated[
            str | None,
            Field(description="Free-text disease name (FTS).", examples=["cardiomyopathy"]),
        ] = None,
        mondo: Annotated[
            str | None,
            Field(
                description="MONDO disease id.", pattern=MONDO_PATTERN, examples=["MONDO:0007254"]
            ),
        ] = None,
        expert_panel: Annotated[
            str | None,
            Field(description="Substring of the curating expert panel name."),
        ] = None,
        classification: Annotated[
            _CLASSIFICATION | None, Field(description="Filter to one classification.")
        ] = None,
        moi: Annotated[_MOI | None, Field(description="Filter to one mode of inheritance.")] = None,
        gene: Annotated[
            str | None,
            Field(description="Restrict to one gene symbol.", examples=["BRCA1"]),
        ] = None,
        page: Annotated[int, Field(ge=1, le=1000, description="1-based page number.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size (max 100).")] = 25,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims verbose fields; full keeps everything."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this to search ClinGen gene-disease validity by disease text/MONDO, expert panel, classification, MOI, or gene. Paginated; a `truncated` block appears when more matches exist. Each record carries a recommended_citation. Returns ~2-10kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            resolved_gene = services.gene.resolve(gene) if gene else None
            models, total = await services.validity.search(
                text=disease,
                mondo=mondo,
                gene=resolved_gene or gene,
                expert_panel=expert_panel,
                classification=classification,
                moi=moi,
                page=page,
                size=size,
            )
            records = shape_records(models, domain="validity", response_mode=response_mode)
            shown = len(records)
            dropped = max(0, total - (page - 1) * size - shown)
            citation = models[0].recommended_citation if models else None
            trunc = (
                truncated_block(
                    kind="pagination",
                    dropped=dropped,
                    to_restore=f"page={page + 1}",
                    to_disable="raise size",
                    filter_applied={
                        k: v
                        for k, v in {
                            "disease": disease,
                            "mondo": mondo,
                            "classification": classification,
                        }.items()
                        if v
                    },
                )
                if dropped > 0
                else None
            )
            next_commands = (
                [cmd("get_gene_validity", gene=models[0].symbol)]
                if models
                else [cmd("get_server_capabilities")]
            )
            return {
                "headline": f"{total} validity assertion(s) match (page {page}, showing {shown}).",
                "records": records,
                "total": total,
                "page": page,
                "size": size,
                "recommended_citation": citation,
                "_meta": build_meta(
                    data_version=data_version_for(services.meta(), "validity"),
                    next_commands=next_commands,
                    record_count=shown,
                    truncated=trunc,
                ),
            }

        return await run_mcp_tool(
            "search_validity",
            call,
            context=McpErrorContext(
                tool_name="search_validity", gene=gene, disease=disease, mondo=mondo
            ),
        )
