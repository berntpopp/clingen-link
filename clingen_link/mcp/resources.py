"""Capabilities and usage payloads for the clingen-link MCP server.

Phase 1 ships a minimal but real capabilities document covering the discovery
and diagnostics tools. Later phases extend `tools`, `datasets`, freshness, and
the citation contract as the domain tools land.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mcp.types import LATEST_PROTOCOL_VERSION as MCP_PROTOCOL_VERSION

RESEARCH_USE_NOTICE = "Research use only; not for clinical decision support."

# Framework citation surfaced in capabilities so an LLM can attribute ClinGen.
CLINGEN_FRAMEWORK_CITATION = (
    "Strande NT, et al. Evaluating the Clinical Validity of Gene-Disease "
    "Associations: An Evidence-Based Framework Developed by the Clinical Genome "
    "Resource. Am J Hum Genet. 2017;100(6):895-906. PMID: 28552198."
)
CLINGEN_LICENSE = "CC BY 4.0 (© ClinGen / Clinical Genome Resource)"


def _server_version() -> str:
    """Return the installed package version, or 'unknown' when not installed."""
    try:
        return version("clingen-link")
    except PackageNotFoundError:
        return "unknown"


def get_capabilities_resource() -> dict[str, Any]:
    """Return the capabilities discovery document.

    Shared by the `get_server_capabilities` tool and the `clingen://capabilities`
    resource so both stay in lock-step.
    """
    return {
        "server": "clingen-link",
        "server_version": _server_version(),
        "mcp_protocol_version": MCP_PROTOCOL_VERSION,
        "research_use_only": True,
        "datasets": {
            "validity": {
                "label": "Gene-Disease Validity",
                "question": "Is gene X causal for disease Y? (Definitive...Refuted)",
            },
            "dosage": {
                "label": "Gene Dosage",
                "question": "Is gene/region haploinsufficient or triplosensitive?",
            },
            "actionability": {
                "label": "Clinical Actionability",
                "question": "Is gene-condition X medically actionable (adult/pediatric)?",
            },
            "erepo": {
                "label": "Variant Pathogenicity (ERepo)",
                "question": "Expert-panel ACMG classification of variant V.",
            },
        },
        "recommended_workflows": [
            "gene symbol -> search_genes -> get_gene_summary",
            "gene summary -> drill into validity/dosage/actionability/erepo",
            "variant -> get_variant_interpretation",
        ],
        "tools": [
            "get_server_capabilities",
            "get_clingen_diagnostics",
        ],
        "resources": {
            "clingen://capabilities": "This capabilities document (JSON).",
            "clingen://research-use": "Research-use notice (JSON).",
        },
        "error_codes": [
            "not_found",
            "invalid_input",
            "rate_limited",
            "validation_failed",
            "upstream_unavailable",
            "snapshot_unavailable",
            "output_validation_failed",
            "internal_error",
        ],
        "parameter_conventions": {
            "response_mode": "minimal | compact | standard | full (default compact)",
        },
        "citation": {
            "framework": CLINGEN_FRAMEWORK_CITATION,
            "license": CLINGEN_LICENSE,
        },
        "limitations": [
            "Phase 1 scaffold: only discovery and diagnostics tools are registered.",
            "Domain tools (validity/dosage/actionability/erepo) land in later phases.",
        ],
        "research_use_notice": RESEARCH_USE_NOTICE,
    }


def get_research_use_resource() -> dict[str, Any]:
    """Return the research-use notice payload."""
    return {"notice": RESEARCH_USE_NOTICE}
