"""Pure response shapers that project domain models into MCP payload shapes.

Each domain has four ``response_mode`` projections plus a shared ``truncated``
block builder:

* ``minimal`` — headline + key counts only (no per-record lists).
* ``compact`` (default) — per-record dicts with null / verbose fields dropped.
* ``standard`` — full per-record dicts (nulls kept) but no extra verbose blocks.
* ``full`` — everything, including the verbose evidence/PMID lists.

Shapers take Pydantic models (from :mod:`clingen_link.models`) and return plain
``dict`` / ``list`` structures. They never touch ``_meta`` — the tools own the
envelope. Keeping the projections here means every tool drops the same fields in
``compact`` mode, so token cost is predictable across the surface.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .hgvs_select import canonical_hgvs

ResponseMode = str

# Per-domain fields that are verbose enough to drop in compact mode (kept in
# standard/full). These are the big evidence/PMID/HGVS lists and the free-text
# summary that dominate token cost.
_VERBOSE_FIELDS: dict[str, frozenset[str]] = {
    "validity": frozenset({"sop", "report_id", "affiliate_id"}),
    "dosage": frozenset({"haplo_pmids", "triplo_pmids", "haplo_description", "triplo_description"}),
    "actionability": frozenset({"adult_sepio_iri", "pediatric_sepio_iri"}),
    "erepo": frozenset(
        {"evidence_codes_met", "evidence_codes_not_met", "pubmed", "summary", "uuid"}
    ),
}

# Per-domain identifying array that is trimmed to a canonical few (+ a ``*_count``) in the
# minimal/compact tiers and kept whole in standard/full (assessment M2).
_ARRAY_TRIM: dict[str, str] = {"erepo": "hgvs"}


def _trim_arrays(row: dict[str, Any], domain: str) -> dict[str, Any]:
    """Replace a domain's big identifying array with a canonical few + a count (compact tiers)."""
    field = _ARRAY_TRIM.get(domain)
    if not field or field not in row:
        return row
    full = row.get(field)
    if isinstance(full, list) and len(full) > 3:
        row = dict(row)
        row[f"{field}_count"] = len(full)
        row[field] = canonical_hgvs(full)
    return row


def _dump(model: BaseModel | dict[str, Any]) -> dict[str, Any]:
    """Return a plain dict for a model or pass a dict through unchanged."""
    if isinstance(model, BaseModel):
        return model.model_dump()
    return dict(model)


def _drop_nulls(row: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values and empty lists/strings (compact-mode trimming)."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if value is None:
            continue
        if isinstance(value, (list, str, dict)) and len(value) == 0:
            continue
        out[key] = value
    return out


def shape_record(
    model: BaseModel | dict[str, Any], *, domain: str, response_mode: ResponseMode
) -> dict[str, Any]:
    """Project one domain record according to ``response_mode``.

    ``permalink`` and ``recommended_citation`` are always preserved (the citation
    contract); ``compact`` drops nulls + the domain's verbose fields; ``standard``
    keeps every field but drops the verbose blocks; ``full`` keeps everything.
    """
    row = _dump(model)
    if response_mode == "full":
        return row
    verbose = _VERBOSE_FIELDS.get(domain, frozenset())
    # minimal and compact share the same record projection so the verbosity tiers form a strict
    # subset lattice (minimal ⊆ compact ⊆ standard ⊆ full). Previously ``minimal`` fell through to
    # the standard branch and kept nulls, making it *more* verbose than compact (assessment M3).
    if response_mode in ("compact", "minimal"):
        trimmed = {k: v for k, v in row.items() if k not in verbose}
        trimmed = _trim_arrays(trimmed, domain)
        kept = _drop_nulls(trimmed)
        # Citation fields are load-bearing; never let _drop_nulls strip them.
        for must in ("permalink", "recommended_citation"):
            if must in row:
                kept[must] = row[must]
        return kept
    # standard: keep nulls, drop only the verbose blocks.
    return {k: v for k, v in row.items() if k not in verbose}


def shape_records(
    models: list[Any], *, domain: str, response_mode: ResponseMode
) -> list[dict[str, Any]]:
    """Project a list of domain records (empty when ``minimal``)."""
    if response_mode == "minimal":
        return []
    return [shape_record(m, domain=domain, response_mode=response_mode) for m in models]


def truncated_block(
    *,
    kind: str,
    dropped: int,
    to_disable: str | None = None,
    to_restore: str | None = None,
    filter_applied: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical ``truncated`` block describing dropped records.

    ``kind`` names what was truncated (e.g. ``"pagination"``); ``dropped`` is the
    count omitted; ``to_disable`` / ``to_restore`` are human-readable hints for
    widening the call (e.g. raising ``size`` or paging); ``filter`` echoes the
    active filters so an LLM can re-call deterministically.
    """
    block: dict[str, Any] = {"kind": kind, "dropped": dropped}
    if to_disable is not None:
        block["to_disable"] = to_disable
    if to_restore is not None:
        block["to_restore"] = to_restore
    block["filter"] = filter_applied or {}
    return block
