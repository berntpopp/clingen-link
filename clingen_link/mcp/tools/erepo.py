"""ERepo variant-pathogenicity tools: list + single-variant ACMG detail.

``get_variant_interpretations`` lists expert-panel interpretations from the
snapshot (CAID, canonical HGVS, MONDO, classification, VCEP, dates, permalink).
``get_variant_interpretation`` returns the full ACMG evidence (codes Met / Not
Met, outcome, guideline/CSpec, PubMed, permalink) preferring the snapshot, with
``refresh=true`` forcing a live SEPIO fetch. Every record carries a verbatim
``recommended_citation``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from clingen_link.exceptions import DataNotFoundError, UpstreamInputError
from clingen_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from clingen_link.mcp.envelope import build_meta, data_version_for
from clingen_link.mcp.errors import McpErrorContext, run_mcp_tool
from clingen_link.mcp.next_commands import cmd
from clingen_link.mcp.patterns import CAID_PATTERN, HGVS_PATTERN
from clingen_link.mcp.schema_relax import relax_output_schema
from clingen_link.mcp.service_adapters import ClingenServices
from clingen_link.mcp.shaping import (
    collect_fenced_objects,
    shape_record,
    shape_records,
    truncated_block,
)
from clingen_link.mcp.untrusted_content import enforce_untrusted_text_limits
from clingen_link.mcp.untrusted_schema import UNTRUSTED_TEXT_OR_NULL, record_items
from clingen_link.models.models import VariantInterpretation
from clingen_link.store import queries

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]
_CLASSIFICATION = Literal[
    "Pathogenic",
    "Likely Pathogenic",
    "Uncertain Significance",
    "Likely Benign",
    "Benign",
]

_LIST_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            # Each record's `summary` is a fenced untrusted_text object (v1.1).
            "records": {"type": "array", "items": record_items("summary")},
            "total": {"type": "integer"},
            "page": {"type": "integer"},
            "size": {"type": "integer"},
            "recommended_citation": {"type": ["string", "null"]},
            "_meta": {"type": "object"},
        },
    }
)

_DETAIL_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            # The interpretation's `summary` is a fenced untrusted_text object (v1.1).
            "interpretation": {
                "type": "object",
                "properties": {"summary": UNTRUSTED_TEXT_OR_NULL},
            },
            "source": {"type": "string"},
            "notice": {"type": ["string", "null"]},
            "recommended_citation": {"type": ["string", "null"]},
            "_meta": {"type": "object"},
        },
    }
)


_AFFILIATION_RE = re.compile(r"/affiliation/(\d+)")


def cspec_next_command(
    guideline_cspec: str | None,
    *,
    gene: str | None,
    resolve: Callable[[str, str | None], list[str]],
) -> dict[str, Any] | None:
    """Build the ERepo->CSpec next_commands entry from a record's guideline_cspec + gene.

    Emits a precise ``{gn_id}`` when ``(affiliation, gene)`` resolves to exactly one
    published spec; otherwise ``{affiliation, gene}`` so the consumer sees candidates.
    Returns None when there is no affiliation to key on.
    """
    if not guideline_cspec:
        return None
    m = _AFFILIATION_RE.search(guideline_cspec)
    if m is None:
        return None
    affiliation = m.group(1)
    gn_ids = resolve(affiliation, gene)
    if len(gn_ids) == 1:
        return cmd("get_cspec", gn_id=gn_ids[0])
    args: dict[str, Any] = {"affiliation": affiliation}
    if gene:
        args["gene"] = gene
    return cmd("get_cspec", **args)


def register_erepo_tools(mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]) -> None:
    """Register get_variant_interpretations + get_variant_interpretation on ``mcp``."""

    @mcp.tool(
        name="get_variant_interpretations",
        title="List ERepo Variant Interpretations",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_LIST_SCHEMA,
        tags={"erepo", "variant"},
    )
    async def get_variant_interpretations(
        gene_symbol: Annotated[
            str | None,
            Field(description="Gene symbol (resolve with search_genes first).", examples=["BRCA1"]),
        ] = None,
        disease: Annotated[
            str | None,
            Field(
                description="Disease text (FTS) or MONDO id.",
                examples=["MONDO:0700268", "cardiomyopathy"],
            ),
        ] = None,
        expert_panel: Annotated[
            str | None,
            Field(description="Substring of the curating VCEP name.", examples=["ENIGMA"]),
        ] = None,
        classification: Annotated[
            _CLASSIFICATION | None,
            Field(description="Filter to one ACMG classification."),
        ] = None,
        page: Annotated[int, Field(ge=1, le=1000, description="1-based page number.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size (max 100).")] = 25,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(
                description="compact (default) trims evidence-code/PubMed lists; full keeps them."
            ),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this to list ClinGen ERepo expert-panel variant interpretations by gene, disease (disease text/MONDO), expert panel, or classification. Returns each variant's CAID, canonical HGVS, MONDO, ACMG classification, VCEP, dates, and permalink. Drill into one with get_variant_interpretation. Paginated. Returns ~2-12kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            resolved_gene = services.gene.resolve(gene_symbol) if gene_symbol else None
            mondo = disease if disease and disease.startswith("MONDO:") else None
            text = disease if disease and not mondo else None
            models, total = await services.erepo.search(
                text=text,
                gene=resolved_gene or gene_symbol,
                mondo=mondo,
                expert_panel=expert_panel,
                assertion=classification,
                page=page,
                size=size,
            )
            records = shape_records(models, domain="erepo", response_mode=response_mode)
            # List-bearing (page size up to 100); v1.1 limit backstop over the fenced summaries.
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
                            "gene_symbol": gene_symbol,
                            "disease": disease,
                            "expert_panel": expert_panel,
                            "classification": classification,
                        }.items()
                        if v
                    },
                )
                if dropped > 0
                else None
            )
            first_caid = next((m.caid for m in models if m.caid), None)
            next_commands = (
                [cmd("get_variant_interpretation", caid=first_caid)]
                if first_caid
                else [cmd("search_genes", query=gene_symbol or disease or "BRCA1")]
            )
            return {
                "headline": (
                    f"{total} ERepo interpretation(s) match (page {page}, showing {shown})."
                ),
                "records": records,
                "total": total,
                "page": page,
                "size": size,
                "recommended_citation": citation,
                "_meta": build_meta(
                    data_version=data_version_for(services.meta(), "erepo"),
                    next_commands=next_commands,
                    record_count=shown,
                    truncated=trunc,
                ),
            }

        return await run_mcp_tool(
            "get_variant_interpretations",
            call,
            context=McpErrorContext(
                tool_name="get_variant_interpretations", gene=gene_symbol, query=disease
            ),
        )

    @mcp.tool(
        name="get_variant_interpretation",
        title="Get ERepo Variant ACMG Detail",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_DETAIL_SCHEMA,
        tags={"erepo", "variant"},
    )
    async def get_variant_interpretation(
        caid: Annotated[
            str | None,
            Field(
                description="ClinGen Allele Registry id.",
                pattern=CAID_PATTERN,
                examples=["CA003783"],
            ),
        ] = None,
        hgvs: Annotated[
            str | None,
            Field(
                description="HGVS expression (genomic/coding/protein).",
                pattern=HGVS_PATTERN,
                examples=["NM_007294.4:c.68_69del"],
            ),
        ] = None,
        clinvar_variation_id: Annotated[
            str | None,
            Field(
                description="ClinVar VariationID (matched against the snapshot).",
                examples=["17662"],
            ),
        ] = None,
        refresh: Annotated[
            bool,
            Field(description="Bypass the snapshot and fetch the live SEPIO interpretation."),
        ] = False,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims verbose blocks; full keeps every field."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this for the full ACMG interpretation of one expert-panel variant: evidence codes Met / Not Met, the classification outcome, guideline/CSpec, PubMed evidence, and the permalink. Supply caid, hgvs, or clinvar_variation_id. refresh=true bypasses the snapshot for the live SEPIO JSON. Returns ~2-8kB."""

        async def call() -> dict[str, Any]:
            selectors = [s for s in (caid, hgvs, clinvar_variation_id) if s]
            if len(selectors) != 1:
                raise UpstreamInputError(
                    "Supply exactly one of caid, hgvs, or clinvar_variation_id."
                )
            services = service_factory()
            # clinvar_variation_id is snapshot-only (the live API keys on caid/hgvs).
            if clinvar_variation_id and not refresh:
                model = _by_clinvar(services, clinvar_variation_id)
                source, notice = "snapshot", None
            else:
                model, source, notice = await services.erepo.get_interpretation(
                    caid=caid, hgvs=hgvs, refresh=refresh
                )
            shaped = shape_record(model, domain="erepo", response_mode=response_mode)
            # minimal omits the per-record body (headline + source only), matching the other tools.
            interpretation = {} if response_mode == "minimal" else shaped
            # Single-record surface (one summary max) -- the default v1.1 object-count ceiling.
            enforce_untrusted_text_limits(collect_fenced_objects(interpretation))
            headline = (
                f"{model.caid or model.gene or 'variant'}: {model.assertion or 'n/a'}"
                + (f" by {model.expert_panel}" if model.expert_panel else "")
                + "."
            )
            next_cmds = [cmd("get_variant_interpretations", gene_symbol=model.gene or "BRCA1")]
            extra = cspec_next_command(
                model.guideline_cspec,
                gene=model.gene,
                resolve=services.cspec_resolve_sync,
            )
            if extra is not None:
                next_cmds.append(extra)
            meta_block = build_meta(
                data_version=data_version_for(services.meta(), "erepo"),
                next_commands=next_cmds,
            )
            if notice:
                meta_block["notice"] = notice
            return {
                "headline": headline,
                "interpretation": interpretation,
                "source": source,
                "notice": notice,
                "recommended_citation": model.recommended_citation,
                "_meta": meta_block,
            }

        return await run_mcp_tool(
            "get_variant_interpretation",
            call,
            context=McpErrorContext(tool_name="get_variant_interpretation", caid=caid, hgvs=hgvs),
        )


def _by_clinvar(services: ClingenServices, clinvar_variation_id: str) -> VariantInterpretation:
    """Resolve a snapshot interpretation by ClinVar VariationID, or raise not_found."""
    with services.store.connection() as conn:
        row = queries.erepo_by_clinvar_id(conn, clinvar_variation_id)
    if row is None:
        raise DataNotFoundError(
            f"No ERepo interpretation for ClinVar VariationID {clinvar_variation_id}."
        )
    return VariantInterpretation.from_row(row)
