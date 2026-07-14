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
from clingen_link.mcp.envelope import build_meta, data_version_for, pagination
from clingen_link.mcp.errors import McpErrorContext, run_mcp_tool
from clingen_link.mcp.filters import Identifier, ensure_identifier
from clingen_link.mcp.next_commands import cmd
from clingen_link.mcp.patterns import GENE_SYMBOL_PATTERN
from clingen_link.mcp.service_adapters import ClingenServices
from clingen_link.mcp.shaping import (
    collect_fenced_objects,
    shape_records,
    truncated_block,
)
from clingen_link.mcp.untrusted_content import enforce_untrusted_text_limits
from clingen_link.models.models import DosageRecord
from clingen_link.vocab import DOSAGE_NOT_EVALUATED, DOSAGE_SCORE_TEXT, DosageScoreCode

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]

# TOOL-SURFACE-BUDGET v1 (B1/B2): outputSchema is suppressed on every tool. It was 60% of
# this server's 14,519-token surface — a per-request tax on a field the MCP spec makes
# OPTIONAL and no model reads. `structuredContent` is unaffected: FastMCP still emits it for
# any dict return, and every tool here returns the dict envelope.

# The score filters' vocabulary is declared as an `enum` (via `DosageScoreCode`), so an
# unrecognised code is REJECTED by the schema instead of silently matching nothing
# (TOOL-SCHEMA-DOCUMENTATION-STANDARD S4; issue #46 D1). A prose description of the
# vocabulary is not enough — it must be machine-declared.
_SCORE_EXAMPLES = ["3", "30"]

# Identifier filters, validated against the snapshot's own index before the search runs.
_REGION = Identifier(
    param="region", table="dosage", column="isca_id", resolver="search_dosage (query=...)"
)
_CYTOBAND = Identifier(
    param="cytoband",
    table="dosage",
    column="cytoband",
    match="prefix",
    resolver="search_dosage (query=...)",
)


def _interpret(score: str | None, description: str | None) -> str | None:
    """Return plain-English text for a score code, or the un-evaluated sentinel.

    ``score`` is a code or ``None``; the prose lives in its own fields
    (``haplo_score`` is numeric-or-null, never a sentence — issue #46 D2). When the
    code is absent, upstream's own ``Not yet evaluated`` marker is surfaced so the
    caller can tell "not evaluated" from "no dosage record" in every response_mode.
    """
    if score:
        return DOSAGE_SCORE_TEXT.get(score, score)
    if description and description.strip() == DOSAGE_NOT_EVALUATED:
        return DOSAGE_NOT_EVALUATED
    return None


def _annotate(record: dict[str, Any], model: DosageRecord) -> dict[str, Any]:
    """Attach haplo/triplo interpretation text to a shaped dosage record.

    Reads the description off the *model*, not the shaped record: the shaped copy has
    been fenced (v1.1 ``untrusted_text``) and compact mode drops it entirely.
    """
    record["haplo_interpretation"] = _interpret(model.haplo_score, model.haplo_description)
    record["triplo_interpretation"] = _interpret(model.triplo_score, model.triplo_description)
    return record


def _shape_dosage(models: list[DosageRecord], response_mode: str) -> list[dict[str, Any]]:
    """Shape dosage records, attaching the interpretation to every detail-bearing tier.

    ``minimal`` carries stable identifiers only (Response-Envelope v1), so there is no
    score field there to interpret.
    """
    records = shape_records(models, domain="dosage", response_mode=response_mode)
    if response_mode == "minimal":
        return records
    return [_annotate(r, m) for r, m in zip(records, models, strict=True)]


def _headline(symbol: str, models: list[DosageRecord]) -> str:
    """One-line dosage summary, read off the models so every response_mode agrees."""
    if not models:
        return f"{symbol}: no ClinGen dosage record."
    first = models[0]
    haplo = _interpret(first.haplo_score, first.haplo_description) or "n/a"
    triplo = _interpret(first.triplo_score, first.triplo_description) or "n/a"
    return f"{symbol} dosage — haploinsufficiency: {haplo}; triplosensitivity: {triplo}."


def register_dosage_tools(mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]) -> None:
    """Register get_gene_dosage + search_dosage on ``mcp``."""

    @mcp.tool(
        name="get_gene_dosage",
        title="Get Gene Dosage Sensitivity",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"dosage"},
    )
    async def get_gene_dosage(
        gene_symbol: Annotated[
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
            symbol = services.gene.resolve(gene_symbol)
            if symbol is None:
                raise DataNotFoundError(
                    f"Gene '{gene_symbol}' is not in the ClinGen snapshot. "
                    "Resolve with search_genes."
                )
            models = await services.dosage.for_gene(symbol)
            # The gene resolved; an empty domain is success+0 (not not_found) — that is reserved for
            # a gene absent from the index entirely (assessment M5).
            records = _shape_dosage(models, response_mode)
            # List-bearing (not paginated); v1.1 limit backstop over the fenced haplo/triplo text.
            enforce_untrusted_text_limits(collect_fenced_objects(records), max_objects=10000)
            citation = models[0].recommended_citation if models else None
            headline = _headline(symbol, models)
            return {
                "headline": headline,
                "records": records,
                "total": len(models),
                "recommended_citation": citation,
                "_meta": build_meta(
                    data_version=data_version_for(services.meta(), "dosage"),
                    next_commands=[
                        cmd("get_gene_summary", gene_symbol=symbol),
                        cmd("get_gene_validity", gene_symbol=symbol),
                    ],
                    record_count=len(models),
                ),
            }

        return await run_mcp_tool(
            "get_gene_dosage",
            call,
            context=McpErrorContext(tool_name="get_gene_dosage", gene=gene_symbol),
        )

    @mcp.tool(
        name="search_dosage",
        title="Search Gene/Region Dosage",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
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
            DosageScoreCode | None,
            Field(
                description=(
                    "Exact ClinGen haploinsufficiency score CODE (not its description): "
                    "0 no evidence, 1 little, 2 some, 3 sufficient evidence; "
                    "30 gene associated with an autosomal-recessive phenotype; "
                    "40 dosage sensitivity unlikely. 30 and 40 are flags, not 'more than 3'."
                ),
                examples=_SCORE_EXAMPLES,
            ),
        ] = None,
        triplo_score: Annotated[
            DosageScoreCode | None,
            Field(
                description=(
                    "Exact ClinGen triplosensitivity score CODE (same scale as haplo_score). "
                    "Genes upstream has not evaluated carry no code and are not matched by any."
                ),
                examples=_SCORE_EXAMPLES,
            ),
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
            # An identifier that matches nothing anywhere is a value to FIX, not an empty
            # result to believe (issue #46). The score filters are schema `enum`s, so an
            # unrecognised code never reaches here at all.
            for spec, value in (
                (_REGION, region),
                (_CYTOBAND, cytoband),
            ):
                ensure_identifier(services.store, spec, value)
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
            records = _shape_dosage(models, response_mode)
            # List-bearing (page size up to 100); v1.1 limit backstop over the fenced text.
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
                [cmd("get_gene_dosage", gene_symbol=first_gene)]
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
                    record_count=shown,
                    pagination_block=pagination(total=total, page=page, size=size, shown=shown),
                    truncated=trunc,
                ),
            }

        return await run_mcp_tool(
            "search_dosage",
            call,
            context=McpErrorContext(tool_name="search_dosage", query=query),
        )
