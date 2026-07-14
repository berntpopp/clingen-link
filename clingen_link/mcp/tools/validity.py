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
from clingen_link.mcp.envelope import build_meta, data_version_for, pagination
from clingen_link.mcp.errors import McpErrorContext, ToolReturn, run_mcp_tool
from clingen_link.mcp.filters import Identifier, ensure_gene, ensure_identifier
from clingen_link.mcp.next_commands import cmd
from clingen_link.mcp.patterns import GENE_SYMBOL_PATTERN, MONDO_PATTERN
from clingen_link.mcp.service_adapters import ClingenServices
from clingen_link.mcp.shaping import collect_fenced_objects, shape_records, truncated_block
from clingen_link.mcp.untrusted_content import enforce_untrusted_text_limits

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]

# TOOL-SURFACE-BUDGET v1 (B1/B2): outputSchema is suppressed on every tool. It was 60% of
# this server's 14,519-token surface — a per-request tax on a field the MCP spec makes
# OPTIONAL and no model reads. `structuredContent` is unaffected: FastMCP still emits it for
# any dict return, and every tool here returns the dict envelope.
_CLASSIFICATION = Literal[
    "Definitive",
    "Strong",
    "Moderate",
    "Limited",
    "Disputed",
    "Refuted",
    "No Known Disease Relationship",
]
# The ClinGen validity feed's MOI codes, verbatim. "Undetermined" was advertised here and
# stored NOWHERE: the feed writes "UD", so the documented value matched zero rows forever —
# a silently-empty filter with a DECLARED enum (issue #46). An enum must name the values the
# runtime can actually match.
_MOI = Literal["AD", "AR", "XL", "MT", "SD", "UD"]

# Identifier filters, validated against the snapshot's index before the search runs.
_DISEASE = Identifier(
    param="disease",
    table="validity_fts",
    column="disease_name",
    match="fts",
    resolver="search_validity (query the disease with a broader term)",
)
_MONDO = Identifier(
    param="mondo", table="validity", column="mondo", resolver="search_validity (disease=...)"
)
_PANEL = Identifier(
    param="expert_panel",
    table="validity",
    column="expert_panel",
    match="like",
    resolver="list_expert_panels",
)


def register_validity_tools(
    mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]
) -> None:
    """Register get_gene_validity + search_validity on ``mcp``."""

    @mcp.tool(
        name="get_gene_validity",
        title="Get Gene-Disease Validity",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"validity"},
    )
    async def get_gene_validity(
        gene_symbol: Annotated[
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
    ) -> ToolReturn:
        """Use this to list ClinGen gene-disease validity assertions (Definitive…Refuted) for one gene, optionally filtered by classification or mode of inheritance. Each record carries a CGGV permalink + recommended_citation. Returns ~1-6kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            symbol = services.gene.resolve(gene_symbol)
            if symbol is None:
                raise DataNotFoundError(
                    f"Gene '{gene_symbol}' is not in the ClinGen snapshot. "
                    "Resolve with search_genes."
                )
            models = await services.validity.for_gene(
                symbol, classification=classification, moi=moi
            )
            records = shape_records(models, domain="validity", response_mode=response_mode)
            # List-bearing (not paginated -- a gene's full assertion set); v1.1 limit backstop.
            enforce_untrusted_text_limits(collect_fenced_objects(records), max_objects=10000)
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
                        cmd("get_gene_summary", gene_symbol=symbol),
                        cmd("get_variant_interpretations", gene_symbol=symbol),
                    ],
                    record_count=len(models),
                ),
            }

        return await run_mcp_tool(
            "get_gene_validity",
            call,
            context=McpErrorContext(tool_name="get_gene_validity", gene=gene_symbol),
        )

    @mcp.tool(
        name="search_validity",
        title="Search Gene-Disease Validity",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
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
        gene_symbol: Annotated[
            str | None,
            Field(description="Restrict to one gene symbol.", examples=["BRCA1"]),
        ] = None,
        page: Annotated[int, Field(ge=1, le=1000, description="1-based page number.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size (max 100).")] = 25,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims verbose fields; full keeps everything."),
        ] = "compact",
    ) -> ToolReturn:
        """Use this to search ClinGen gene-disease validity by disease text/MONDO, expert panel, classification, MOI, or gene. Paginated; a `truncated` block appears when more matches exist. Each record carries a recommended_citation. Returns ~2-10kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            # Reject a filter value the snapshot cannot match, instead of returning zero rows
            # that read as "ClinGen has no such assertion" (issue #46).
            resolved_gene = ensure_gene(services.store, gene_symbol)
            for spec, value in (
                (_DISEASE, disease),
                (_MONDO, mondo),
                (_PANEL, expert_panel),
            ):
                ensure_identifier(services.store, spec, value)
            models, total = await services.validity.search(
                text=disease,
                mondo=mondo,
                gene=resolved_gene,
                expert_panel=expert_panel,
                classification=classification,
                moi=moi,
                page=page,
                size=size,
            )
            records = shape_records(models, domain="validity", response_mode=response_mode)
            # List-bearing (page size up to 100); v1.1 limit backstop over the fenced disease names.
            enforce_untrusted_text_limits(collect_fenced_objects(records), max_objects=10000)
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
                [cmd("get_gene_validity", gene_symbol=models[0].symbol)]
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
                    pagination_block=pagination(total=total, page=page, size=size, shown=shown),
                    truncated=trunc,
                ),
            }

        return await run_mcp_tool(
            "search_validity",
            call,
            context=McpErrorContext(
                tool_name="search_validity", gene=gene_symbol, disease=disease, mondo=mondo
            ),
        )
