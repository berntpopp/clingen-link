"""Hand-authored FastMCP facade for clingen-link."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from clingen_link.mcp.errors import install_validation_error_handler
from clingen_link.mcp.output_validation import install_output_validation_error_handler
from clingen_link.mcp.resources import RESEARCH_USE_NOTICE
from clingen_link.mcp.service_adapters import ClingenServices, get_services
from clingen_link.mcp.tools import register_clingen_tools

_INSTRUCTIONS = (
    "clingen-link grounds gene/disease/variant questions in ClinGen's four "
    "curated datasets: gene-disease validity, gene dosage, clinical "
    "actionability, and variant pathogenicity (ERepo).\n"
    "- Canonical workflow: search_genes (resolve a symbol/HGNC/alias to a "
    "canonical gene) -> get_gene_summary (one-call cross-domain overview) -> "
    "drill into a domain (validity / dosage / actionability / erepo) -> "
    "get_variant_interpretation for a specific variant's ACMG classification.\n"
    "- Validity answers 'is gene X causal for disease Y?' (Definitive...Refuted); "
    "dosage answers haploinsufficiency / triplosensitivity; actionability answers "
    "adult/pediatric medical actionability; ERepo holds expert-panel ACMG variant "
    "classifications.\n"
    "- Chaining: every response carries _meta.next_commands, a ready-to-call list "
    "of {tool, arguments} next steps (on success and on error); execute the first "
    "entry to advance without guessing the next tool.\n"
    "- Citations: records carry a recommended_citation string; paste it verbatim.\n"
    "- Discovery: call get_server_capabilities or read clingen://capabilities. "
    f"{RESEARCH_USE_NOTICE}"
)


def create_clingen_mcp(
    service_factory: Callable[[], ClingenServices] | None = None,
) -> FastMCP:
    """Build the clingen-link MCP server.

    ``service_factory`` is a lazy callable so HTTP mode can defer to
    ``app.state`` and stdio mode can hold a directly constructed instance. When
    omitted it defaults to the process-wide :func:`get_services` singleton.
    """
    factory = service_factory or get_services
    mcp = FastMCP(
        name="clingen-link",
        instructions=_INSTRUCTIONS,
        mask_error_details=True,
    )
    register_clingen_tools(mcp, service_factory=factory)
    install_validation_error_handler(mcp)
    install_output_validation_error_handler(mcp)
    return mcp
