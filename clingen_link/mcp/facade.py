"""Hand-authored FastMCP facade for clingen-link."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from clingen_link import __version__
from clingen_link.mcp.errors import install_validation_error_handler
from clingen_link.mcp.output_validation import install_output_validation_error_handler
from clingen_link.mcp.resources import RESEARCH_USE_NOTICE
from clingen_link.mcp.service_adapters import ClingenServices, get_services
from clingen_link.mcp.tools import register_clingen_tools

_INSTRUCTIONS = (
    "clingen-link grounds gene/disease/variant questions in ClinGen's five "
    "curated datasets: gene-disease validity, gene dosage, clinical "
    "actionability, variant pathogenicity (ERepo), and VCEP criteria "
    "specifications (CSpec).\n"
    "- Canonical workflow: search_genes (resolve a symbol/HGNC/alias to a "
    "canonical gene) -> get_gene_summary (one-call cross-domain overview) -> "
    "drill into a domain (get_gene_validity / get_gene_dosage / "
    "get_gene_actionability / get_variant_interpretations) -> "
    "get_variant_interpretation for one variant's full ACMG evidence -> get_cspec "
    "for the VCEP's ACMG/AMP rule set (criteria codes, strengths, guidance "
    "files).\n"
    "- Validity answers 'is gene X causal for disease Y?' (Definitive...Refuted); "
    "dosage answers haploinsufficiency / triplosensitivity; actionability answers "
    "adult/pediatric medical actionability (include_detail=true fetches live "
    "SEPIO); ERepo holds expert-panel ACMG variant classifications "
    "(get_variant_interpretation refresh=true fetches live SEPIO).\n"
    "- Criteria specifications: list_cspecs / get_cspec / get_cspec_criterion / "
    "search_cspec expose the gene-specific ACMG/AMP rules each VCEP applies; an "
    "ERepo variant links to its CSpec via affiliation+gene.\n"
    "- Search across domains with search_validity / search_dosage / "
    "search_actionability / get_variant_interpretations / search_cspec; resolve a "
    "curating panel with list_expert_panels.\n"
    "- Payload control: response_mode = minimal | compact (default) | standard | "
    "full. Search tools paginate (page + size) and emit _meta.truncated when rows "
    "are dropped.\n"
    "- Chaining: every response carries _meta.next_commands, a ready-to-call list "
    "of {tool, arguments} next steps (on success and on error); execute the first "
    "entry to advance without guessing the next tool.\n"
    "- Citations: every record carries a verbatim recommended_citation + permalink; "
    "paste it without paraphrasing. Framework citation + license: clingen://citations.\n"
    "- Discovery: call get_server_capabilities or read clingen://capabilities; "
    "clingen://reference holds the error taxonomy + truncation contract + field "
    "glossary; clingen://freshness holds the per-domain snapshot version. "
    f"{RESEARCH_USE_NOTICE}"
)


def create_clingen_mcp(
    service_factory: Callable[[], ClingenServices] | None = None,
) -> FastMCP:
    """Build the clingen-link MCP server.

    ``service_factory`` is a lazy callable so the FastAPI host can defer to
    ``app.state`` (per-request shared services). When omitted it defaults to the
    process-wide :func:`get_services` singleton.
    """
    factory = service_factory or get_services
    mcp = FastMCP(
        name="clingen-link",
        version=__version__,
        instructions=_INSTRUCTIONS,
        mask_error_details=True,
    )
    register_clingen_tools(mcp, service_factory=factory)
    install_validation_error_handler(mcp)
    install_output_validation_error_handler(mcp)
    return mcp
