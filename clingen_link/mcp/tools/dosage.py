"""Gene dosage tools: get_gene_dosage + search_dosage (snapshot).

Adds plain-English interpretation text for the ClinGen haplo/triplo score codes
(0…3 evidence scale plus the special "unlikely" / "AR phenotype" markers) so an
LLM does not have to memorise the scale. Both-build coordinates, disease/MONDO,
and PMIDs come straight from the model. Every record carries a permalink +
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
from clingen_link.mcp.shaping import shape_record, shape_records, truncated_block

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]

# ClinGen dosage evidence scale → plain-English interpretation. Keys cover the
# numeric 0-3 scale plus the textual special codes seen in the snapshot.
_SCORE_TEXT: dict[str, str] = {
    "0": "No evidence",
    "1": "Little evidence",
    "2": "Some evidence (emerging)",
    "3": "Sufficient evidence for dosage pathogenicity",
    "30": "Gene associated with autosomal recessive phenotype",
    "40": "Dosage sensitivity unlikely",
    "Dosage sensitivity unlikely": "Dosage sensitivity unlikely",
    "Gene associated with autosomal recessive phenotype": (
        "Gene associated with autosomal recessive phenotype"
    ),
}

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


def _interpret(score: str | None) -> str | None:
    """Return interpretation text for a haplo/triplo score code, or None."""
    if score is None or score == "":
        return None
    return _SCORE_TEXT.get(str(score), str(score))


def _annotate(record: dict[str, Any]) -> dict[str, Any]:
    """Attach haplo/triplo interpretation text to a shaped dosage record."""
    record["haplo_interpretation"] = _interpret(record.get("haplo_score"))
    record["triplo_interpretation"] = _interpret(record.get("triplo_score"))
    return record


def register_dosage_tools(mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]) -> None:
    """Register get_gene_dosage + search_dosage on ``mcp``."""

    @mcp.tool(
        name="get_gene_dosage",
        title="Get Gene Dosage Sensitivity",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_LIST_SCHEMA,
        tags={"dosage"},
    )
    async def get_gene_dosage(
        gene: Annotated[
            str,
            Field(
                description="Gene symbol or HGNC id (resolve with search_genes first).",
                min_length=1,
                max_length=64,
                pattern=GENE_SYMBOL_PATTERN + r"|^HGNC:\d+$",
                examples=["BRCA1", "AAGAB"],
            ),
        ],
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims verbose fields; full keeps the PMID lists."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this for ClinGen dosage sensitivity of a gene: haploinsufficiency + triplosensitivity score with plain-English interpretation, GRCh37/GRCh38 coordinates, disease/MONDO, and PMIDs. Resolve free text with search_genes first. Returns ~1-3kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            symbol = services.gene.resolve(gene)
            if symbol is None:
                raise DataNotFoundError(
                    f"Gene '{gene}' is not in the ClinGen snapshot. Resolve with search_genes."
                )
            models = await services.dosage.for_gene(symbol)
            if not models:
                raise DataNotFoundError(f"No ClinGen dosage record for '{symbol}'.")
            records = [
                _annotate(shape_record(m, domain="dosage", response_mode=response_mode))
                for m in models
            ]
            citation = models[0].recommended_citation
            head = records[0]
            headline = (
                f"{symbol} dosage — haploinsufficiency: "
                f"{head.get('haplo_interpretation') or 'n/a'}; triplosensitivity: "
                f"{head.get('triplo_interpretation') or 'n/a'}."
            )
            return {
                "headline": headline,
                "records": records,
                "total": len(records),
                "recommended_citation": citation,
                "_meta": build_meta(
                    data_version=data_version_for(services.meta(), "dosage"),
                    next_commands=[
                        cmd("get_gene_summary", gene=symbol),
                        cmd("get_gene_validity", gene=symbol),
                    ],
                    recommended_citation=citation,
                    record_count=len(records),
                ),
            }

        return await run_mcp_tool(
            "get_gene_dosage",
            call,
            context=McpErrorContext(tool_name="get_gene_dosage", gene=gene),
        )

    @mcp.tool(
        name="search_dosage",
        title="Search Gene/Region Dosage",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_LIST_SCHEMA,
        tags={"dosage"},
    )
    async def search_dosage(
        query: Annotated[
            str | None,
            Field(description="Free-text gene / disease / ISCA text (FTS).", examples=["1p36"]),
        ] = None,
        region: Annotated[
            str | None,
            Field(description="ISCA region id.", examples=["ISCA-46291"]),
        ] = None,
        cytoband: Annotated[
            str | None,
            Field(description="Cytoband prefix (e.g. 17q21).", examples=["17q21"]),
        ] = None,
        haplo_score: Annotated[
            str | None,
            Field(description="Exact haploinsufficiency score code (0-3, 30, 40).", examples=["3"]),
        ] = None,
        triplo_score: Annotated[
            str | None,
            Field(description="Exact triplosensitivity score code.", examples=["3"]),
        ] = None,
        record_type: Annotated[
            Literal["gene", "region"] | None,
            Field(description="Restrict to gene or region records."),
        ] = None,
        page: Annotated[int, Field(ge=1, le=1000, description="1-based page number.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size (max 100).")] = 25,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims verbose fields; full keeps the PMID lists."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this to search ClinGen dosage (genes + regions) by text, ISCA region, cytoband, or haplo/triplo score, optionally restricted to gene or region records. Paginated; a `truncated` block appears when more matches exist. Returns ~2-10kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            models, total = await services.dosage.search(
                text=query,
                isca_id=region,
                cytoband=cytoband,
                haplo_score=haplo_score,
                triplo_score=triplo_score,
                record_type=record_type,
                page=page,
                size=size,
            )
            records = [
                _annotate(r)
                for r in shape_records(models, domain="dosage", response_mode=response_mode)
            ]
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
                            "query": query,
                            "cytoband": cytoband,
                            "record_type": record_type,
                            "haplo_score": haplo_score,
                        }.items()
                        if v
                    },
                )
                if dropped > 0
                else None
            )
            first_gene = next((m.symbol for m in models if m.symbol), None)
            next_commands = (
                [cmd("get_gene_dosage", gene=first_gene)]
                if first_gene
                else [cmd("get_server_capabilities")]
            )
            return {
                "headline": f"{total} dosage record(s) match (page {page}, showing {shown}).",
                "records": records,
                "total": total,
                "page": page,
                "size": size,
                "recommended_citation": citation,
                "_meta": build_meta(
                    data_version=data_version_for(services.meta(), "dosage"),
                    next_commands=next_commands,
                    recommended_citation=citation,
                    record_count=shown,
                    truncated=trunc,
                ),
            }

        return await run_mcp_tool(
            "search_dosage",
            call,
            context=McpErrorContext(tool_name="search_dosage", query=query),
        )
