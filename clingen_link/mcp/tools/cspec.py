"""CSpec criteria-specification tools: list / get / criterion / search.

ClinGen Variant Curation Expert Panels (VCEPs) publish *criteria
specifications* (CSpecs) that adapt the ACMG/AMP framework to a gene or disease.
These four tools expose the snapshot's CSpec catalog:

``list_cspecs`` browses spec headers (filter by gene/affiliation/status);
``get_cspec`` returns one spec with its genes, criteria, and file catalog;
``get_cspec_criterion`` returns a single criterion's strength rules + files;
``search_cspec`` runs FTS across spec labels, criteria, and filenames.

Each ``_*_impl`` coroutine wraps itself in :func:`run_mcp_tool`, so callers (and
the registered ``@mcp.tool`` shims) always receive the canonical envelope --
``success``, ``_meta.unsafe_for_clinical_use``, ``next_commands``, and a verbatim
``recommended_citation`` -- whether invoked directly or through FastMCP.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from clingen_link.exceptions import AmbiguousQueryError, DataNotFoundError
from clingen_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from clingen_link.mcp.envelope import build_meta, data_version_for
from clingen_link.mcp.errors import McpErrorContext, run_mcp_tool
from clingen_link.mcp.next_commands import cmd
from clingen_link.mcp.patterns import GENE_SYMBOL_PATTERN, GN_ID_PATTERN
from clingen_link.mcp.schema_relax import relax_output_schema
from clingen_link.mcp.service_adapters import ClingenServices, get_services
from clingen_link.mcp.shaping import (
    collect_fenced_objects,
    shape_record,
    shape_records,
    truncated_block,
)
from clingen_link.mcp.untrusted_content import enforce_untrusted_text_limits
from clingen_link.mcp.untrusted_schema import CSPEC_RECORD_SCHEMA

_RESPONSE_MODE = Literal["minimal", "compact", "standard", "full"]

# Intentionally a permissive SUPERSET schema shared by all four cspec tools: record | records |
# total | page | size never all coexist in one response, and relax_output_schema keeps it additive.
# criteria[*].description and criteria[*].strengths[*].description are fenced untrusted_text (v1.1).
_DETAIL_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "record": CSPEC_RECORD_SCHEMA,
            "records": {"type": "array", "items": CSPEC_RECORD_SCHEMA},
            "total": {"type": "integer"},
            "page": {"type": "integer"},
            "size": {"type": "integer"},
            "recommended_citation": {"type": ["string", "null"]},
            "_meta": {"type": "object"},
        },
    }
)


def register_cspec_tools(mcp: FastMCP, *, service_factory: Callable[[], ClingenServices]) -> None:
    """Register list_cspecs + get_cspec + get_cspec_criterion + search_cspec on ``mcp``."""

    @mcp.tool(
        name="list_cspecs",
        title="List ClinGen Criteria Specifications",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_DETAIL_SCHEMA,
        tags={"cspec"},
    )
    async def list_cspecs(
        gene_symbol: Annotated[
            str | None,
            Field(
                description="Gene symbol covered by the spec (resolve with search_genes first).",
                pattern=GENE_SYMBOL_PATTERN,
                examples=["BRCA1"],
            ),
        ] = None,
        affiliation: Annotated[
            str | None,
            Field(
                description="ClinGen affiliation id of the curating VCEP.",
                examples=["50087"],
            ),
        ] = None,
        status: Annotated[
            str | None,
            Field(
                description="CSpec lifecycle status filter (cspecStatus).",
                examples=["Released"],
            ),
        ] = None,
        page: Annotated[int, Field(ge=1, le=1000, description="1-based page number.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size (max 100).")] = 25,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) drops nulls + verbose header fields."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this to browse ClinGen criteria-specification (CSpec) headers, filtered by gene, curating affiliation (VCEP), or lifecycle status. Each row carries the GN id, affiliation, label, version, and status. Drill into one with get_cspec. Paginated; returns ~1-8kB."""
        return await _list_cspecs_impl(
            gene=gene_symbol,
            affiliation=affiliation,
            status=status,
            page=page,
            size=size,
            response_mode=response_mode,
            service_factory=service_factory,
        )

    @mcp.tool(
        name="get_cspec",
        title="Get ClinGen Criteria Specification Detail",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_DETAIL_SCHEMA,
        tags={"cspec"},
    )
    async def get_cspec(
        gn_id: Annotated[
            str | None,
            Field(
                description="CSpec GN identifier.",
                pattern=GN_ID_PATTERN,
                examples=["GN092"],
            ),
        ] = None,
        affiliation: Annotated[
            str | None,
            Field(
                description="ClinGen affiliation id (resolves the VCEP's spec(s)).",
                examples=["50087"],
            ),
        ] = None,
        gene_symbol: Annotated[
            str | None,
            Field(
                description="Gene symbol (narrows an affiliation, or finds spec(s) covering it).",
                pattern=GENE_SYMBOL_PATTERN,
                examples=["BRCA1"],
            ),
        ] = None,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims verbose header fields; full keeps them."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this for one criteria specification in full: its genes/diseases, every ACMG/AMP criterion with strength rules, and the attached guidance files. Supply gn_id, or an affiliation (optionally narrowed by gene), or a gene. Returns ~3-30kB depending on the spec."""
        return await _get_cspec_impl(
            gn_id=gn_id,
            affiliation=affiliation,
            gene=gene_symbol,
            response_mode=response_mode,
            service_factory=service_factory,
        )

    @mcp.tool(
        name="get_cspec_criterion",
        title="Get One CSpec Criterion",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_DETAIL_SCHEMA,
        tags={"cspec"},
    )
    async def get_cspec_criterion(
        criteria_id: Annotated[
            str | None,
            Field(
                description="Direct criterion id (from a get_cspec / search_cspec hit).",
                examples=["55"],
            ),
        ] = None,
        gn_id: Annotated[
            str | None,
            Field(
                description="CSpec GN id (required with code when criteria_id is absent).",
                pattern=GN_ID_PATTERN,
                examples=["GN092"],
            ),
        ] = None,
        code: Annotated[
            str | None,
            Field(
                description="ACMG/AMP code (with gn_id) to resolve the criterion.",
                examples=["PVS1"],
            ),
        ] = None,
        rule_set_id: Annotated[
            str | None,
            Field(
                description="Disambiguate a code shared across multiple rule sets.",
                examples=["9"],
            ),
        ] = None,
        response_mode: Annotated[
            _RESPONSE_MODE,
            Field(description="compact (default) trims nulls; full keeps every field."),
        ] = "compact",
    ) -> dict[str, Any]:
        """Use this for a single CSpec criterion's specification: its ACMG/AMP code, description, the VCEP's strength rules, and any attached evidence files. Supply criteria_id directly, or gn_id + code (add rule_set_id when a code spans multiple rule sets). Returns ~1-4kB."""
        return await _get_criterion_impl(
            criteria_id=criteria_id,
            gn_id=gn_id,
            code=code,
            rule_set_id=rule_set_id,
            response_mode=response_mode,
            service_factory=service_factory,
        )

    @mcp.tool(
        name="search_cspec",
        title="Search ClinGen Criteria Specifications",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=_DETAIL_SCHEMA,
        tags={"cspec"},
    )
    async def search_cspec(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=256,
                description="Full-text query across spec labels, criteria, and filenames.",
                examples=["ENIGMA", "PVS1 null variant"],
            ),
        ],
        page: Annotated[int, Field(ge=1, le=1000, description="1-based page number.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size (max 100).")] = 25,
    ) -> dict[str, Any]:
        """Use this to full-text search the CSpec catalog (spec labels, criteria descriptions, attachment filenames). Each hit names its entity_type + ids so you can chain into get_cspec or get_cspec_criterion. Paginated; returns ~1-6kB."""
        return await _search_cspec_impl(
            query=query, page=page, size=size, service_factory=service_factory
        )


async def _list_cspecs_impl(
    *,
    gene: str | None,
    affiliation: str | None,
    status: str | None,
    page: int,
    size: int,
    response_mode: _RESPONSE_MODE,
    service_factory: Callable[[], ClingenServices] = get_services,
) -> dict[str, Any]:
    """List CSpec headers (wrapped envelope)."""

    async def call() -> dict[str, Any]:
        services = service_factory()
        models, total = await services.cspec.list_specs(
            gene=gene, affiliation=affiliation, status=status, page=page, size=size
        )
        records = shape_records(models, domain="cspec", response_mode=response_mode)
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
                        "gene_symbol": gene,
                        "affiliation": affiliation,
                        "status": status,
                    }.items()
                    if v
                },
            )
            if dropped > 0
            else None
        )
        next_commands = (
            [cmd("get_cspec", gn_id=models[0].gn_id)]
            if models
            else [cmd("search_cspec", query=gene or affiliation or "ENIGMA")]
        )
        return {
            "headline": f"{total} criteria specification(s) match (page {page}, showing {shown}).",
            "records": records,
            "total": total,
            "page": page,
            "size": size,
            "recommended_citation": citation,
            "_meta": build_meta(
                data_version=data_version_for(services.meta(), "cspec"),
                next_commands=next_commands,
                record_count=shown,
                truncated=trunc,
            ),
        }

    return await run_mcp_tool(
        "list_cspecs",
        call,
        context=McpErrorContext(tool_name="list_cspecs", gene=gene),
    )


async def _resolve_gn_ids(
    services: ClingenServices,
    *,
    gn_id: str | None,
    affiliation: str | None,
    gene: str | None,
) -> list[str]:
    """Resolve the GN id(s) a get_cspec call addresses (gn_id → affiliation → gene)."""
    if gn_id:
        return [gn_id]
    if affiliation:
        return await services.cspec.resolve_for_erepo(affiliation_id=affiliation, gene=gene)
    if gene:
        models, _ = await services.cspec.list_specs(gene=gene, page=1, size=100)
        return [m.gn_id for m in models]
    raise DataNotFoundError("Supply one of gn_id, affiliation, or gene.")


async def _get_cspec_impl(
    *,
    gn_id: str | None,
    affiliation: str | None,
    gene: str | None,
    response_mode: _RESPONSE_MODE,
    service_factory: Callable[[], ClingenServices] = get_services,
) -> dict[str, Any]:
    """Return one (or several) full CSpec detail(s) (wrapped envelope)."""

    async def call() -> dict[str, Any]:
        services = service_factory()
        gn_ids = await _resolve_gn_ids(services, gn_id=gn_id, affiliation=affiliation, gene=gene)
        details = []
        for gid in gn_ids:
            detail = await services.cspec.get_detail(gn_id=gid)
            if detail is not None:
                details.append(detail)
        if not details:
            raise DataNotFoundError(
                f"No criteria specification for {gn_id or affiliation or gene}."
            )
        first = details[0]
        first_criterion = first.criteria[0].criteria_id if first.criteria else None
        next_commands = (
            [cmd("get_cspec_criterion", criteria_id=first_criterion)]
            if first_criterion
            else [cmd("list_cspecs", page=1, size=25)]
        )
        meta = build_meta(
            data_version=data_version_for(services.meta(), "cspec"),
            next_commands=next_commands,
            record_count=len(details),
        )
        out: dict[str, Any] = {
            "headline": (
                f"{first.gn_id}: {first.label or 'criteria specification'}"
                + (f" ({first.affiliation_label})" if first.affiliation_label else "")
                + "."
            ),
            "recommended_citation": first.recommended_citation,
            "_meta": meta,
        }
        if len(details) == 1:
            out["record"] = shape_record(first, domain="cspec", response_mode=response_mode)
        else:
            out["records"] = shape_records(details, domain="cspec", response_mode=response_mode)
            out["total"] = len(details)
        # A spec's criteria list is not paginated and could in principle be dozens; v1.1
        # limit backstop over every fenced description (criterion + nested strengths).
        enforce_untrusted_text_limits(
            collect_fenced_objects(out.get("record"), out.get("records")), max_objects=10000
        )
        return out

    return await run_mcp_tool(
        "get_cspec",
        call,
        context=McpErrorContext(tool_name="get_cspec", gene=gene),
    )


async def _get_criterion_impl(
    *,
    criteria_id: str | None,
    gn_id: str | None,
    code: str | None,
    rule_set_id: str | None,
    response_mode: _RESPONSE_MODE,
    service_factory: Callable[[], ClingenServices] = get_services,
) -> dict[str, Any]:
    """Return one CSpec criterion (wrapped envelope)."""

    async def call() -> dict[str, Any]:
        services = service_factory()
        resolved_id = criteria_id
        if resolved_id is None:
            if not (gn_id and code):
                raise DataNotFoundError("Supply criteria_id, or both gn_id and code.")
            ids = await services.cspec.resolve_criterion_ids(
                gn_id=gn_id, code=code, rule_set_id=rule_set_id
            )
            if len(ids) > 1:
                raise AmbiguousQueryError(
                    f"{len(ids)} criteria match (gn_id={gn_id}, code={code}); "
                    "supply criteria_id or rule_set_id to disambiguate."
                )
            if not ids:
                raise DataNotFoundError(
                    f"No criterion for gn_id={gn_id}, code={code} in the ClinGen snapshot."
                )
            resolved_id = ids[0]
        criterion = await services.cspec.get_criterion(criteria_id=resolved_id)
        if criterion is None:
            raise DataNotFoundError(f"No criterion {resolved_id}.")
        meta = build_meta(
            data_version=data_version_for(services.meta(), "cspec"),
            next_commands=[cmd("get_cspec", gn_id=criterion.gn_id)],
        )
        record = shape_record(criterion, domain="cspec", response_mode=response_mode)
        # A criterion's strength levels are a list; v1.1 limit backstop over the fenced text.
        enforce_untrusted_text_limits(collect_fenced_objects(record), max_objects=10000)
        return {
            "headline": f"{criterion.code} in {criterion.gn_id}.",
            "record": record,
            "recommended_citation": None,
            "_meta": meta,
        }

    return await run_mcp_tool(
        "get_cspec_criterion",
        call,
        context=McpErrorContext(tool_name="get_cspec_criterion", gene=gn_id),
    )


async def _search_cspec_impl(
    *,
    query: str,
    page: int,
    size: int,
    service_factory: Callable[[], ClingenServices] = get_services,
) -> dict[str, Any]:
    """Full-text search the CSpec catalog (wrapped envelope)."""

    async def call() -> dict[str, Any]:
        services = service_factory()
        hits, total = await services.cspec.search(text=query, page=page, size=size)
        shown = len(hits)
        dropped = max(0, total - (page - 1) * size - shown)
        trunc = (
            truncated_block(
                kind="pagination",
                dropped=dropped,
                to_restore=f"page={page + 1}",
                to_disable="raise size",
                filter_applied={"query": query},
            )
            if dropped > 0
            else None
        )
        next_commands = _search_next_commands(hits)
        return {
            "headline": f"{total} CSpec hit(s) for '{query}' (page {page}, showing {shown}).",
            "records": hits,
            "total": total,
            "page": page,
            "size": size,
            "recommended_citation": None,
            "_meta": build_meta(
                data_version=data_version_for(services.meta(), "cspec"),
                next_commands=next_commands,
                record_count=shown,
                truncated=trunc,
            ),
        }

    return await run_mcp_tool(
        "search_cspec",
        call,
        context=McpErrorContext(tool_name="search_cspec", query=query),
    )


def _search_next_commands(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chain into the first hit: a criterion hit → get_cspec_criterion, else → get_cspec."""
    for hit in hits:
        criteria_id = hit.get("criteria_id")
        if criteria_id:
            return [cmd("get_cspec_criterion", criteria_id=str(criteria_id))]
        gn_id = hit.get("gn_id")
        if gn_id:
            return [cmd("get_cspec", gn_id=str(gn_id))]
    return [cmd("list_cspecs", page=1, size=25)]
