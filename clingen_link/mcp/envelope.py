"""Shared helpers for building the success ``_meta`` block on clingen-link tools.

Every successful tool response carries the same provenance contract: a
``data_version`` + ``fetched_at`` pulled from the snapshot ``meta`` rows,
``next_commands`` (ready-to-call follow-ups), a verbatim ``recommended_citation``
(from the service models), and ``unsafe_for_clinical_use``. Centralising the
assembly keeps the contract identical across the domain tools and out of each
600-LOC tool module.
"""

from __future__ import annotations

from typing import Any

# Per-domain → the snapshot meta-table key whose row carries the freshness signal
# surfaced as data_version. The gene hub spans all four, so it folds them.
_DOMAIN_META_KEY: dict[str, str] = {
    "validity": "validity",
    "dosage": "dosage",
    "actionability": "actionability",
    "erepo": "erepo",
}


def data_version_for(meta: dict[str, dict[str, Any]], domain: str) -> dict[str, Any]:
    """Return ``{domain, version, fetched_at, record_count}`` from a meta map.

    ``meta`` is :meth:`ClingenServices.meta` output (per-domain freshness rows).
    Missing domains degrade to ``None`` values rather than raising so a partial
    snapshot never breaks a tool envelope.
    """
    key = _DOMAIN_META_KEY.get(domain, domain)
    row = meta.get(key) or {}
    return {
        "domain": domain,
        "version": row.get("signal_value"),
        "snapshot_version": row.get("snapshot_version"),
        "fetched_at": row.get("fetched_at"),
        "record_count": row.get("record_count"),
        "source_url": row.get("source_url"),
    }


def cross_domain_version(meta: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return a folded per-domain version map for the gene hub (all four domains)."""
    return {domain: data_version_for(meta, domain) for domain in _DOMAIN_META_KEY}


def build_meta(
    *,
    data_version: dict[str, Any],
    next_commands: list[dict[str, Any]],
    record_count: int | None = None,
    truncated: dict[str, Any] | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the canonical success ``_meta`` block.

    ``unsafe_for_clinical_use`` is injected by ``run_mcp_tool`` for every response, so it is
    omitted here. The ``recommended_citation`` is deliberately NOT emitted in ``_meta``: the
    load-bearing copies live per-record (and as the single top-level summary citation on
    detail/hub tools), so re-stating it in the envelope was pure duplication (assessment M4).
    """
    meta: dict[str, Any] = {
        "data_version": data_version,
        "next_commands": next_commands,
    }
    if fetched_at is not None:
        meta["fetched_at"] = fetched_at
    elif isinstance(data_version, dict) and data_version.get("fetched_at"):
        meta["fetched_at"] = data_version["fetched_at"]
    if record_count is not None:
        meta["record_count"] = record_count
    if truncated is not None:
        meta["truncated"] = truncated
    return meta
