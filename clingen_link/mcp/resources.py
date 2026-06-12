"""Capabilities, usage, reference, freshness, citations payloads for clingen-link.

The capabilities document is the discovery surface: every registered tool, the
per-domain dataset version/freshness (folded in from the snapshot ``meta`` rows),
token-cost hints, the error taxonomy, parameter conventions, the resources map,
and a ``capabilities_version`` sha256 of the payload so a warm client can skip
re-fetching an unchanged descriptor. The framework citation + license live in
``clingen://citations``.
"""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mcp.types import LATEST_PROTOCOL_VERSION

# Re-exported for the diagnostics tool and capabilities document.
MCP_PROTOCOL_VERSION: str = LATEST_PROTOCOL_VERSION

RESEARCH_USE_NOTICE = "Research use only; not for clinical decision support."

# Framework citation surfaced in capabilities so an LLM can attribute ClinGen.
CLINGEN_FRAMEWORK_CITATION = (
    "Strande NT, et al. Evaluating the Clinical Validity of Gene-Disease "
    "Associations: An Evidence-Based Framework Developed by the Clinical Genome "
    "Resource. Am J Hum Genet. 2017;100(6):895-906. PMID: 28552198."
)
CLINGEN_LICENSE = "CC BY 4.0 (© ClinGen / Clinical Genome Resource)"

# Every tool registered by register_clingen_tools, with a token-cost hint.
_TOOLS: dict[str, str] = {
    "get_server_capabilities": "~4kB (discovery surface; detail in clingen://reference)",
    "search_genes": "~1-3kB",
    "get_gene_summary": "compact ~3-8kB (minimal ~0.5kB)",
    "get_gene_validity": "~1-6kB",
    "search_validity": "~2-10kB (size-dependent)",
    "get_gene_dosage": "~1-3kB",
    "search_dosage": "~2-10kB (size-dependent)",
    "get_gene_actionability": "snapshot ~1-4kB; include_detail adds live SEPIO",
    "search_actionability": "~2-8kB (size-dependent)",
    "get_variant_interpretations": "~2-12kB (size-dependent)",
    "get_variant_interpretation": "~2-8kB (refresh fetches live SEPIO)",
    "list_expert_panels": "~1-5kB",
    "get_clingen_diagnostics": "<1kB",
}

_RESOURCES: dict[str, str] = {
    "clingen://capabilities": "This capabilities document (JSON).",
    "clingen://usage": "Workflow + payload-mode + citation guidance (text).",
    "clingen://reference": "Error taxonomy, truncation contract, and field glossary (JSON).",
    "clingen://freshness": "Per-domain snapshot version/date/record counts (JSON).",
    "clingen://research-use": "Research-use notice (JSON).",
    "clingen://citations": "Framework citation + license + per-domain citation contract (JSON).",
}

_ERROR_CODES = [
    "not_found",
    "invalid_input",
    "rate_limited",
    "validation_failed",
    "upstream_unavailable",
    "snapshot_unavailable",
    "output_validation_failed",
    "internal_error",
]

_DATASET_LABELS: dict[str, dict[str, str]] = {
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
}


def _server_version() -> str:
    """Return the installed package version, or 'unknown' when not installed."""
    try:
        return version("clingen-link")
    except PackageNotFoundError:
        return "unknown"


def _datasets(meta: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Merge per-domain labels with snapshot freshness (when ``meta`` is given)."""
    out: dict[str, dict[str, Any]] = {}
    for domain, label in _DATASET_LABELS.items():
        entry: dict[str, Any] = dict(label)
        row = (meta or {}).get(domain)
        if row:
            entry["version"] = row.get("signal_value")
            entry["snapshot_version"] = row.get("snapshot_version")
            entry["fetched_at"] = row.get("fetched_at")
            entry["record_count"] = row.get("record_count")
            entry["source_url"] = row.get("source_url")
        out[domain] = entry
    return out


def get_capabilities_resource(meta: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return the capabilities discovery document.

    ``meta`` is the snapshot ``meta`` map (per-domain freshness). The
    ``capabilities_version`` is a sha256 of the payload (with the hash field
    excluded) so a warm client can compare and skip a re-fetch.
    """
    payload: dict[str, Any] = {
        "server": "clingen-link",
        "server_version": _server_version(),
        "mcp_protocol_version": MCP_PROTOCOL_VERSION,
        "research_use_only": True,
        "datasets": _datasets(meta),
        "recommended_workflows": [
            "gene symbol -> search_genes -> get_gene_summary",
            "gene summary -> drill into validity/dosage/actionability/erepo",
            "variant -> get_variant_interpretation",
            "disease -> search_validity + get_variant_interpretations",
        ],
        "tools": list(_TOOLS),
        "token_cost_hints": dict(_TOOLS),
        "resources": dict(_RESOURCES),
        "error_codes": list(_ERROR_CODES),
        "parameter_conventions": {
            "response_mode": "minimal | compact | standard | full (default compact)",
            "pagination": "page (1-based) + size (<=100); a `truncated` block flags omitted rows",
            "gene_input": "symbol, HGNC id, or alias (resolve with search_genes first)",
            "next_commands": "_meta.next_commands is a ready-to-call list of {tool, arguments}",
        },
        "citation_contract": (
            "Every record carries a verbatim recommended_citation + stable permalink; "
            "paste it without paraphrasing. Framework citation + license in clingen://citations."
        ),
        "limitations": [
            "Snapshot-backed: validity / dosage / actionability / ERepo lists come from the "
            "bundled SQLite snapshot; only single-variant ERepo detail (refresh=true) and "
            "actionability SEPIO (include_detail=true) hit live ClinGen.",
            "Read-only; no curation/write paths.",
            RESEARCH_USE_NOTICE,
        ],
        "research_use_notice": RESEARCH_USE_NOTICE,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    payload["capabilities_version"] = digest
    return payload


def get_usage_resource() -> str:
    """Return the human-readable usage / workflow guide (text)."""
    return (
        "clingen-link usage\n"
        "==================\n"
        "Canonical workflow: search_genes (resolve a symbol/HGNC/alias) -> "
        "get_gene_summary (one-call cross-domain overview) -> drill into a domain "
        "(get_gene_validity / get_gene_dosage / get_gene_actionability / "
        "get_variant_interpretations) -> get_variant_interpretation for one variant's "
        "full ACMG evidence.\n\n"
        "Payload modes: response_mode = minimal | compact | standard | full. Start "
        "compact (default); minimal returns just the headline + counts; full keeps every "
        "verbose field (evidence codes, PMIDs, SEPIO IRIs).\n\n"
        "Pagination: search tools take page (1-based) + size (<=100). When more matches "
        "exist than the page shows, _meta.truncated describes how many were dropped and how "
        "to widen the call.\n\n"
        "Live drill-down: get_gene_actionability(include_detail=true) fetches the live SEPIO "
        "assertion document; get_variant_interpretation(refresh=true) bypasses the snapshot for "
        "the live evidence-code SEPIO. Everything else is served from the offline snapshot.\n\n"
        "Citations: every record carries recommended_citation + permalink — paste verbatim. "
        + RESEARCH_USE_NOTICE
    )


def get_reference_resource() -> dict[str, Any]:
    """Return the error taxonomy + truncation contract + field glossary (JSON)."""
    return {
        "error_codes": {
            "not_found": "Well-formed identifier absent from the snapshot; reformulate or resolve.",
            "invalid_input": "Malformed identifier/query; retrying unchanged cannot succeed.",
            "rate_limited": "Upstream 429 or local concurrency saturation; retry with backoff.",
            "validation_failed": "Arguments failed schema validation; check field_errors.",
            "upstream_unavailable": "A live ClinGen endpoint failed transiently; retry with backoff.",
            "snapshot_unavailable": "Bundled snapshot missing/unreadable; operator runs refresh.",
            "output_validation_failed": "Tool output did not match its declared schema.",
            "internal_error": "Unexpected failure; call get_clingen_diagnostics.",
        },
        "recovery_actions": ["retry_backoff", "reformulate_input", "switch_tool"],
        "truncation_contract": {
            "field": "_meta.truncated",
            "shape": {
                "kind": "what was truncated (e.g. pagination)",
                "dropped": "count of omitted records",
                "to_disable": "hint to widen the call",
                "to_restore": "ready-to-use re-call hint (e.g. page=2)",
                "filter": "echo of the active filters",
            },
        },
        "field_glossary": {
            "classification": "Validity: Definitive..Refuted; ERepo: Pathogenic..Benign (ACMG).",
            "moi": "Mode of inheritance (AD, AR, XL, MT, SD, Undetermined).",
            "haplo_score / triplo_score": "Dosage evidence scale 0-3 (+ special codes 30/40).",
            "perm_id": "CGGV validity permalink token.",
            "caid": "ClinGen Allele Registry id (e.g. CA003783).",
            "evidence_codes_met / not_met": "ACMG criteria the VCEP applied / did not apply.",
            "expert_panel": "Curating ClinGen GCEP/VCEP.",
        },
        "datasets": list(_DATASET_LABELS),
    }


def get_freshness_resource(meta: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return per-domain snapshot freshness (version/date/record counts)."""
    return {
        "research_use_only": True,
        "domains": {
            domain: {
                "version": (row or {}).get("signal_value"),
                "signal_type": (row or {}).get("signal_type"),
                "fetched_at": (row or {}).get("fetched_at"),
                "record_count": (row or {}).get("record_count"),
                "snapshot_version": (row or {}).get("snapshot_version"),
                "source_url": (row or {}).get("source_url"),
            }
            for domain, row in (meta or {}).items()
        },
        "research_use_notice": RESEARCH_USE_NOTICE,
    }


def get_citations_resource() -> dict[str, Any]:
    """Return the framework citation, license, and per-domain citation contract."""
    return {
        "framework_citation": CLINGEN_FRAMEWORK_CITATION,
        "license": CLINGEN_LICENSE,
        "attribution": "© ClinGen / Clinical Genome Resource. Data licensed under CC BY 4.0.",
        "per_domain_permalink": {
            "validity": "CGGV perm_id -> search.clinicalgenome.org/kb/gene-validity/{perm_id}",
            "dosage": "HGNC/ISCA report page -> search.clinicalgenome.org/kb/gene-dosage/{id}",
            "actionability": "AC#### doc id + SEPIO IRI (actionability.clinicalgenome.org)",
            "erepo": "CAR:CAxxxxx + interpretation @id (erepo.clinicalgenome.org)",
        },
        "guidance": (
            "Every tool record carries a verbatim recommended_citation; paste it without "
            "paraphrasing. Cite the framework reference (Strande et al. 2017) when describing "
            "ClinGen methodology."
        ),
        "research_use_notice": RESEARCH_USE_NOTICE,
    }


def get_research_use_resource() -> dict[str, Any]:
    """Return the research-use notice payload."""
    return {"notice": RESEARCH_USE_NOTICE}
