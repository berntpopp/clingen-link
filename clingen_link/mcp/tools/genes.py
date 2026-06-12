"""Gene hub tools: search_genes (resolver) + get_gene_summary (flagship).

``search_genes`` resolves a symbol / HGNC id / alias to a canonical gene and
lists per-domain availability + counts so an LLM can pick the right drill-down.
``get_gene_summary`` is the flagship one-call cross-domain overview: validity
classifications by disease, dosage haplo/triplo, actionability adult/pediatric,
and ERepo variant counts, with ``next_commands`` into each domain tool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from clingen_link.exceptions import DataNotFoundError
from clingen_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from clingen_link.mcp.envelope import build_meta, cross_domain_version, data_version_for
from clingen_link.mcp.errors import McpErrorContext, run_mcp_tool
from clingen_link.mcp.next_commands import cmd
from clingen_link.mcp.patterns import GENE_SYMBOL_PATTERN
from clingen_link.mcp.schema_relax import relax_output_schema
from clingen_link.mcp.service_adapters import ClingenServices
from clingen_link.mcp.shaping import shape_records

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]

_GENE_QUERY = Annotated[
    str,
    Field(
        description="Gene symbol, HGNC id, or alias to resolve (e.g. BRCA1, HGNC:1100).",
        min_length=1,
        max_length=64,
        pattern=GENE_SYMBOL_PATTERN + r"|^HGNC:\d+$",
        examples=["BRCA1", "HGNC:1100", "FANCS"],
    ),
]
_GENE_ARG = Annotated[
    str,
    Field(
        description="Canonical gene symbol or HGNC id (resolve with search_genes first).",
        min_length=1,
        max_length=64,
        pattern=GENE_SYMBOL_PATTERN + r"|^HGNC:\d+$",
        examples=["BRCA1", "HGNC:1100"],
    ),
]

_SEARCH_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "query": {"type": "string"},
            "resolved_symbol": {"type": ["string", "null"]},
            "candidates": {"type": "array", "items": {"type": "object"}},
            "_meta": {"type": "object"},
        },
    }
)

_SUMMARY_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "symbol": {"type": "string"},
            "hgnc_id": {"type": ["string", "null"]},
            "counts": {"type": "object"},
            "validity": {"type": "array", "items": {"type": "object"}},
            "dosage": {"type": "array", "items": {"type": "object"}},
            "actionability": {"type": "array", "items": {"type": "object"}},
            "erepo_variant_count": {"type": "integer"},
            "recommended_citation": {"type": "string"},
            "_meta": {"type": "object"},
        },
    }
)


def _availability(row: dict[str, Any]) -> dict[str, Any]:
    """Project a gene-index row into a compact availability + counts dict."""
    return {
        "symbol": row.get("symbol"),
        "hgnc_id": row.get("hgnc_id"),
        "name": row.get("name"),
        "has_validity": bool(row.get("has_validity")),
        "has_dosage": bool(row.get("has_dosage")),
        "has_actionability": bool(row.get("has_actionability")),
        "erepo_variant_count": int(row.get("erepo_variant_count") or 0),
    }


def register_gene_tools(mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]) -> None:
    """Register search_genes + get_gene_summary on ``mcp``."""

    @mcp.tool(
        name="search_genes",
        title="Search / Resolve Genes",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_SEARCH_SCHEMA,
        tags={"gene"},
    )
    async def search_genes(
        query: _GENE_QUERY,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims null fields; full keeps everything."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this FIRST to resolve a free-text gene (symbol / HGNC id / alias) into a canonical ClinGen gene plus its per-domain availability and counts. Follow the _meta.next_commands into get_gene_summary. Unknown input returns a not_found envelope with a fallback. Returns ~1-3kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            resolved = services.gene.resolve(query)
            candidates = [_availability(r) for r in services.gene.search(query, limit=25)]
            meta = services.meta()
            if resolved is None and not candidates:
                raise DataNotFoundError(
                    f"No ClinGen gene resolves from '{query}'. Check the symbol/HGNC id."
                )
            headline = (
                f"Resolved '{query}' to {resolved}."
                if resolved
                else f"'{query}' did not resolve to one gene; {len(candidates)} candidate(s)."
            )
            next_commands = [cmd("get_gene_summary", gene=resolved or query)]
            return {
                "headline": headline,
                "query": query,
                "resolved_symbol": resolved,
                "candidates": candidates,
                "_meta": build_meta(
                    data_version=cross_domain_version(meta),
                    next_commands=next_commands,
                    record_count=len(candidates),
                ),
            }

        return await run_mcp_tool(
            "search_genes",
            call,
            context=McpErrorContext(tool_name="search_genes", gene=query, query=query),
        )

    @mcp.tool(
        name="get_gene_summary",
        title="Get Gene Cross-Domain Summary",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_SUMMARY_SCHEMA,
        tags={"gene"},
    )
    async def get_gene_summary(
        gene: _GENE_ARG,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(
                description=(
                    "minimal = headline + counts only; compact (default) trims verbose "
                    "fields; standard keeps nulls; full returns every field."
                )
            ),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this for a one-call cross-domain overview of a gene: validity classifications by disease, dosage haplo/triplo scores, actionability adult/pediatric, and ERepo variant counts. Resolve free text with search_genes first. The _meta.next_commands drill into each domain tool. Returns compact ~3-8kB (minimal ~0.5kB)."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            symbol = services.gene.resolve(gene)
            if symbol is None:
                raise DataNotFoundError(
                    f"Gene '{gene}' is not in the ClinGen snapshot. Resolve with search_genes."
                )
            summary = await services.gene.get_summary(symbol)
            if summary is None:
                raise DataNotFoundError(f"Gene '{symbol}' has no ClinGen index row.")
            meta = services.meta()
            counts = {
                "validity": summary.validity_count,
                "dosage": summary.dosage_count,
                "actionability": summary.actionability_count,
                "erepo_variants": summary.erepo_count,
            }
            headline = (
                f"{symbol}: {counts['validity']} validity, {counts['dosage']} dosage, "
                f"{counts['actionability']} actionability, {counts['erepo_variants']} ERepo "
                "variant interpretations."
            )
            next_commands = [
                cmd("get_gene_validity", gene=symbol),
                cmd("get_gene_dosage", gene=symbol),
                cmd("get_gene_actionability", gene=symbol),
                cmd("get_variant_interpretations", gene=symbol),
            ]
            result: dict[str, Any] = {
                "headline": headline,
                "symbol": symbol,
                "hgnc_id": summary.hgnc_id,
                "name": summary.name,
                "counts": counts,
                "erepo_variant_count": summary.erepo_count,
                "recommended_citation": summary.recommended_citation,
                "_meta": build_meta(
                    data_version=cross_domain_version(meta),
                    next_commands=next_commands,
                    recommended_citation=summary.recommended_citation,
                ),
            }
            if response_mode != "minimal":
                result["validity"] = shape_records(
                    summary.validity, domain="validity", response_mode=response_mode
                )
                result["dosage"] = shape_records(
                    summary.dosage, domain="dosage", response_mode=response_mode
                )
                result["actionability"] = shape_records(
                    summary.actionability, domain="actionability", response_mode=response_mode
                )
            return result

        return await run_mcp_tool(
            "get_gene_summary",
            call,
            context=McpErrorContext(tool_name="get_gene_summary", gene=gene),
        )
