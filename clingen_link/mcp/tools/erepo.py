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
from clingen_link.mcp.envelope import build_meta, data_version_for, pagination
from clingen_link.mcp.errors import McpErrorContext, ToolReturn, run_mcp_tool
from clingen_link.mcp.filters import Identifier, ensure_gene, ensure_identifier
from clingen_link.mcp.next_commands import cmd
from clingen_link.mcp.patterns import (
    CAID_PATTERN,
    CLINVAR_VARIATION_ID_PATTERN,
    HGVS_PATTERN,
    VARIANT_ID_PATTERN,
)
from clingen_link.mcp.service_adapters import ClingenServices
from clingen_link.mcp.shaping import (
    collect_fenced_objects,
    shape_record,
    shape_records,
    truncated_block,
)
from clingen_link.mcp.untrusted_content import enforce_untrusted_text_limits
from clingen_link.models.models import VariantInterpretation
from clingen_link.store import queries

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]

# TOOL-SURFACE-BUDGET v1 (B1/B2): outputSchema is suppressed on every tool. It was 60% of
# this server's 14,519-token surface — a per-request tax on a field the MCP spec makes
# OPTIONAL and no model reads. `structuredContent` is unaffected: FastMCP still emits it for
# any dict return, and every tool here returns the dict envelope.
_CLASSIFICATION = Literal[
    "Pathogenic",
    "Likely Pathogenic",
    "Uncertain Significance",
    "Likely Benign",
    "Benign",
]

# Identifier filters, validated against the snapshot's index before the search runs, so an
# unknown gene / disease / panel is an error the model can fix — not zero rows it will report
# as "ClinGen has no interpretations" (issue #46).
_DISEASE_TEXT = Identifier(
    param="disease",
    table="erepo_fts",
    column="disease",
    match="fts",
    resolver="get_variant_interpretations (a broader disease term)",
)
_DISEASE_MONDO = Identifier(
    param="disease",
    table="erepo",
    column="mondo",
    resolver="search_validity (to find the MONDO id)",
)
_PANEL = Identifier(
    param="expert_panel",
    table="erepo",
    column="expert_panel",
    match="like",
    resolver="list_expert_panels",
)


_AFFILIATION_RE = re.compile(r"/affiliation/(\d+)")


def cspec_next_command(
    guideline_cspec: str | None,
    *,
    gene: str | None,
    resolve: Callable[[str, str | None], list[str]],
) -> dict[str, Any] | None:
    """Build the ERepo->CSpec next_commands entry from a record's guideline_cspec + gene.

    Emits a precise ``get_cspec(gn_id)`` when ``(affiliation, gene)`` resolves to exactly one
    published spec; otherwise ``list_cspecs(affiliation, gene_symbol)`` so the consumer browses
    the candidates. get_cspec takes ONLY gn_id now (issue #46), so the ambiguous case must chain
    to the resolver, not re-invoke get_cspec with parameters it no longer accepts. Returns None
    when there is no affiliation to key on.
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
        args["gene_symbol"] = gene
    return cmd("list_cspecs", **args)


def register_erepo_tools(mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]) -> None:
    """Register get_variant_interpretations + get_variant_interpretation on ``mcp``."""

    @mcp.tool(
        name="get_variant_interpretations",
        title="List ERepo Variant Interpretations",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
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
    ) -> ToolReturn:
        """Use this to list ClinGen ERepo expert-panel variant interpretations by gene, disease (disease text/MONDO), expert panel, or classification. Returns each variant's CAID, canonical HGVS, MONDO, ACMG classification, VCEP, dates, and permalink. Drill into one with get_variant_interpretation. Paginated. Returns ~2-12kB."""

        async def call() -> dict[str, Any]:
            services = service_factory()
            resolved_gene = ensure_gene(services.store, gene_symbol)
            mondo = disease if disease and disease.startswith("MONDO:") else None
            text = disease if disease and not mondo else None
            ensure_identifier(services.store, _DISEASE_MONDO, mondo)
            ensure_identifier(services.store, _DISEASE_TEXT, text)
            ensure_identifier(services.store, _PANEL, expert_panel)
            models, total = await services.erepo.search(
                text=text,
                gene=resolved_gene,
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
                [cmd("get_variant_interpretation", variant_id=first_caid)]
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
                    pagination_block=pagination(total=total, page=page, size=size, shown=shown),
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
        output_schema=None,
        tags={"erepo", "variant"},
    )
    async def get_variant_interpretation(
        variant_id: Annotated[
            str,
            Field(
                description=(
                    "The variant to look up, in any ONE of the three identifier shapes ERepo "
                    "keys on: a ClinGen Allele Registry id (CA003783), a ClinVar VariationID "
                    "(17662), or an HGVS expression (NM_007294.4:c.68_69del). The shape is "
                    "detected from the value."
                ),
                min_length=1,
                max_length=256,
                pattern=VARIANT_ID_PATTERN,
                examples=["CA003783", "NM_007294.4:c.68_69del", "17662"],
            ),
        ],
        refresh: Annotated[
            bool,
            Field(description="Bypass the snapshot and fetch the live SEPIO interpretation."),
        ] = False,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims verbose blocks; full keeps every field."),
        ] = "compact",
    ) -> ToolReturn:
        """Use this for the full ACMG interpretation of one expert-panel variant: evidence codes Met / Not Met, the classification outcome, guideline/CSpec, PubMed evidence, and the permalink. variant_id takes a CAID, a ClinVar VariationID, or an HGVS expression. refresh=true bypasses the snapshot for the live SEPIO JSON. Returns ~2-8kB."""

        async def call() -> dict[str, Any]:
            caid, hgvs, clinvar_variation_id = _split_variant_id(variant_id)
            services = service_factory()
            # clinvar_variation_id is snapshot-only (the live API keys on caid/hgvs).
            if clinvar_variation_id and not refresh:
                model = _by_clinvar(services, clinvar_variation_id)
                source, notice = "snapshot", None
            else:
                model, source, notice = await services.erepo.get_interpretation(
                    caid=caid, hgvs=hgvs, refresh=refresh
                )
            # Every tier keeps the record: `minimal` narrows it to identifiers, it does not
            # replace it with {} (issue #46).
            interpretation = shape_record(model, domain="erepo", response_mode=response_mode)
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
            context=McpErrorContext(tool_name="get_variant_interpretation", caid=variant_id),
        )


def _split_variant_id(variant_id: str) -> tuple[str | None, str | None, str | None]:
    """Route one ``variant_id`` to the id kind ERepo needs: (caid, hgvs, clinvar_variation_id).

    The three shapes are mutually exclusive and trivially distinguishable — a CAID starts
    "CA", a ClinVar VariationID is all digits, an HGVS expression has a ``:x.`` body — so the
    caller supplies ONE parameter instead of choosing which of three optional ones to fill.
    """
    value = variant_id.strip()
    if re.match(CAID_PATTERN, value, re.IGNORECASE):
        return value, None, None
    if re.match(CLINVAR_VARIATION_ID_PATTERN, value):
        return None, None, value
    if re.match(HGVS_PATTERN, value):
        return None, value, None
    raise UpstreamInputError(
        "variant_id must be a ClinGen Allele Registry id (CA003783), a ClinVar VariationID "
        "(17662), or an HGVS expression (NM_007294.4:c.68_69del)."
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
