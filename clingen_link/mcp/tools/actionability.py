"""Clinical actionability tools: get_gene_actionability + search_actionability.

Snapshot-backed adult/pediatric assertions (status, release, SEPIO IRIs); with
``include_detail=true`` the gene tool fetches the live SEPIO assertion document
for the requested context. Every record carries the actionability permalink + a
verbatim ``recommended_citation``.
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
from clingen_link.mcp.patterns import GENE_SYMBOL_PATTERN
from clingen_link.mcp.schema_relax import relax_output_schema
from clingen_link.mcp.service_adapters import ClingenServices
from clingen_link.mcp.shaping import (
    collect_fenced_objects,
    fence_untrusted_blob,
    shape_records,
    truncated_block,
)
from clingen_link.mcp.untrusted_content import enforce_untrusted_text_limits
from clingen_link.mcp.untrusted_schema import record_items

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]
_CONTEXT = Literal["Adult", "Pediatric"]

_LIST_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            # sepio_detail (include_detail=true) is the fenced live SEPIO blob.
            "records": {"type": "array", "items": record_items("sepio_detail")},
            "total": {"type": "integer"},
            "page": {"type": "integer"},
            "size": {"type": "integer"},
            "context": {"type": ["string", "null"]},
            "recommended_citation": {"type": ["string", "null"]},
            "_meta": {"type": "object"},
        },
    }
)


def register_actionability_tools(
    mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]
) -> None:
    """Register get_gene_actionability + search_actionability on ``mcp``."""

    @mcp.tool(
        name="get_gene_actionability",
        title="Get Clinical Actionability",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_LIST_SCHEMA,
        tags={"actionability"},
    )
    async def get_gene_actionability(
        gene_symbol: Annotated[
            str,
            Field(
                description="Gene symbol or HGNC id (resolve with search_genes first).",
                min_length=1,
                max_length=64,
                pattern=GENE_SYMBOL_PATTERN + r"|^HGNC:\d+$",
                examples=["BRCA1", "SCN1A"],
            ),
        ],
        context: Annotated[
            _CONTEXT,
            Field(description="Adult (default) or Pediatric assertion context."),
        ] = "Adult",
        include_detail: Annotated[
            bool,
            Field(
                description="Fetch the live SEPIO assertion document for each curation in the "
                "requested context (extra upstream calls). False (default) returns snapshot rows."
            ),
        ] = False,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims SEPIO IRIs; full keeps everything."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this for ClinGen clinical actionability of a gene: adult/pediatric assertion status, release, disease, and SEPIO links. Set include_detail=true to fetch the live SEPIO assertion document for the chosen context. Resolve free text with search_genes first. Returns snapshot ~1-4kB; include_detail adds the live SEPIO payload."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            symbol = services.gene.resolve(gene_symbol)
            if symbol is None:
                raise DataNotFoundError(
                    f"Gene '{gene_symbol}' is not in the ClinGen snapshot. "
                    "Resolve with search_genes."
                )
            models = await services.actionability.for_gene(symbol, context=context)
            # The gene resolved; an empty domain is success+0 (not not_found) — reserved for a gene
            # absent from the index entirely (assessment M5).
            records = shape_records(models, domain="actionability", response_mode=response_mode)
            # `minimal` is the identifiers-only tier (Response-Envelope v1), so the live SEPIO
            # document is not attached there — hanging a multi-kB blob off a "minimal" record
            # would contradict the mode the caller asked for.
            if include_detail and response_mode != "minimal":
                for model, record in zip(models, records, strict=True):
                    # The live SEPIO document is raw upstream JSON with nested external
                    # prose; fence the whole blob as one opaque untrusted_text object
                    # (typed + size-bounded) rather than passing the raw payload through.
                    detail = await services.actionability.sepio_detail(model.doc_id, context)
                    record["sepio_detail"] = fence_untrusted_blob(
                        detail, source="clingen", record_id=f"{model.doc_id}#{context}"
                    ).model_dump(mode="json")
                # Whole-response v1.1 limit backstop over the fenced SEPIO blobs.
                enforce_untrusted_text_limits(collect_fenced_objects(records), max_objects=10000)
            citation = models[0].recommended_citation if models else None
            headline = (
                f"{symbol}: {len(models)} actionability curation(s) ({context} context)."
                if models
                else f"{symbol}: no ClinGen actionability curation in the {context} context."
            )
            return {
                "headline": headline,
                "records": records,
                "total": len(models),
                "context": context,
                "recommended_citation": citation,
                "_meta": build_meta(
                    data_version=data_version_for(services.meta(), "actionability"),
                    next_commands=[
                        cmd("get_gene_summary", gene_symbol=symbol),
                        cmd("get_gene_validity", gene_symbol=symbol),
                    ],
                    record_count=len(models),
                ),
            }

        return await run_mcp_tool(
            "get_gene_actionability",
            call,
            context=McpErrorContext(tool_name="get_gene_actionability", gene=gene_symbol),
        )

    @mcp.tool(
        name="search_actionability",
        title="Search Clinical Actionability",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_LIST_SCHEMA,
        tags={"actionability"},
    )
    async def search_actionability(
        disease: Annotated[
            str | None,
            Field(description="Free-text disease name (FTS).", examples=["melanoma"]),
        ] = None,
        gene_symbol: Annotated[
            str | None,
            Field(description="Gene symbol that the curation lists.", examples=["SCN1A"]),
        ] = None,
        context: Annotated[
            _CONTEXT | None,
            Field(description="Seed the citation with this assertion context."),
        ] = None,
        assertion: Annotated[
            str | None,
            Field(description="Filter by assertion outcome text (post-filter)."),
        ] = None,
        page: Annotated[int, Field(ge=1, le=1000, description="1-based page number.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size (max 100).")] = 25,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims SEPIO IRIs; full keeps everything."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this to search ClinGen clinical actionability by disease text or gene. Paginated; a `truncated` block appears when more matches exist. Each record carries the actionability permalink + recommended_citation. Returns ~2-8kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            resolved_gene = services.gene.resolve(gene_symbol) if gene_symbol else None
            models, total = await services.actionability.search(
                text=disease, gene=resolved_gene or gene_symbol, page=page, size=size
            )
            if assertion:
                models = [
                    m
                    for m in models
                    if assertion.lower()
                    in f"{m.adult_status or ''} {m.pediatric_status or ''}".lower()
                ]
            records = shape_records(models, domain="actionability", response_mode=response_mode)
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
                        for k, v in {"disease": disease, "gene_symbol": gene_symbol}.items()
                        if v
                    },
                )
                if dropped > 0
                else None
            )
            first_gene = next(
                (g for m in models for g in m.genes if g), resolved_gene or gene_symbol
            )
            next_commands = (
                [cmd("get_gene_actionability", gene_symbol=first_gene)]
                if first_gene
                else [cmd("get_server_capabilities")]
            )
            return {
                "headline": f"{total} actionability curation(s) match (page {page}, showing {shown}).",
                "records": records,
                "total": total,
                "page": page,
                "size": size,
                "context": context,
                "recommended_citation": citation,
                "_meta": build_meta(
                    data_version=data_version_for(services.meta(), "actionability"),
                    next_commands=next_commands,
                    record_count=shown,
                    truncated=trunc,
                ),
            }

        return await run_mcp_tool(
            "search_actionability",
            call,
            context=McpErrorContext(
                tool_name="search_actionability", gene=gene_symbol, disease=disease
            ),
        )
