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

import json
from typing import Any

from pydantic import BaseModel

from .hgvs_select import canonical_hgvs
from .untrusted_content import UntrustedText, fence_untrusted_text

ResponseMode = str


def fence_untrusted_blob(obj: Any, *, source: str, record_id: str) -> UntrustedText:
    """Fence an arbitrary upstream JSON document as one opaque ``untrusted_text`` leaf.

    Used for raw upstream blobs whose internal structure is unknown/unstable (e.g. the
    live SEPIO actionability document): the whole document — and every prose field nested
    anywhere inside it — is external untrusted content, so it is serialized to a single
    deterministic JSON string and fenced as one typed object. ``raw_sha256`` is over that
    serialized string's bytes; the router treats the whole subtree opaque. This both types
    the blob as data and (via ``enforce_untrusted_text_limits``) bounds its size, instead
    of emitting an unfenced, unbounded upstream payload.
    """
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return fence_untrusted_text(raw, source=source, record_id=record_id)


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
    "cspec": frozenset({"current_status", "affiliation_id"}),
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


# Response-Envelope Standard v1.1: externally sourced free-text fields are fenced as the
# typed ``untrusted_text`` object at this shaping boundary -- never a bare string -- in
# every response_mode. ``_DOSAGE_PROSE_FIELDS`` lists DosageRecord's two prose fields;
# validity/erepo fence a single named field (handled inline in ``_fence_domain_fields``).
_DOSAGE_PROSE_FIELDS = ("haplo_description", "triplo_description")


def _fence_cspec_criterion(criterion: dict[str, Any]) -> dict[str, Any]:
    """Fence one CriteriaCode row's ``description`` + nested ``strengths[*].description``.

    ``record_id`` is the rule-set id (falling back to the spec's GN id when a criterion
    carries no rule_set_id) plus the ACMG/AMP code, e.g. ``9:PVS1`` -- precise enough to
    re-retrieve the criterion via ``get_cspec_criterion``. A criterion's own free-text spec
    and each evidence-strength's optional spec text share the same upstream CSpec
    provenance, so both are fenced (the strengths field is not in the inventory's literal
    pointer list but is the same class of upstream prose).
    """
    record_id = (
        f"{criterion.get('rule_set_id') or criterion.get('gn_id') or 'unknown'}:"
        f"{criterion.get('code') or 'unknown'}"
    )
    if criterion.get("description"):
        criterion["description"] = fence_untrusted_text(
            criterion["description"], source="clingen", record_id=record_id
        ).model_dump(mode="json")
    strengths = criterion.get("strengths")
    if isinstance(strengths, list):
        for strength in strengths:
            if isinstance(strength, dict) and strength.get("description"):
                strength["description"] = fence_untrusted_text(
                    strength["description"], source="clingen", record_id=record_id
                ).model_dump(mode="json")
    return criterion


def _fence_domain_fields(row: dict[str, Any], domain: str) -> dict[str, Any]:
    """Replace a domain's externally sourced prose field(s) with the v1.1 fenced object.

    Runs on the raw dumped row *before* any response_mode trimming, so a prose field that
    survives trimming is always the typed ``untrusted_text`` object, never a bare string --
    across minimal/compact/standard/full alike. ``record_id`` uses each domain's own stable
    identifier (never the router, never a synthetic counter):

    - validity: the CGGV assertion id (``perm_id``), falling back to the gene symbol.
    - dosage: the gene symbol/HGNC id/ISCA id (haplo + triplo share one gene record).
    - erepo: the interpretation's CAID, falling back to its ERepo uuid / ClinVar id.
    - cspec: the rule-set id + ACMG/AMP code (see ``_fence_cspec_criterion``).
    """
    if domain == "validity" and row.get("disease_name"):
        record_id = str(row.get("perm_id") or row.get("symbol") or "unknown")
        row["disease_name"] = fence_untrusted_text(
            row["disease_name"], source="clingen", record_id=record_id
        ).model_dump(mode="json")
    elif domain == "dosage":
        record_id = str(row.get("symbol") or row.get("hgnc_id") or row.get("isca_id") or "unknown")
        for field in _DOSAGE_PROSE_FIELDS:
            if row.get(field):
                row[field] = fence_untrusted_text(
                    row[field], source="clingen", record_id=record_id
                ).model_dump(mode="json")
    elif domain == "erepo" and row.get("summary"):
        record_id = str(
            row.get("caid") or row.get("uuid") or row.get("clinvar_variation_id") or "unknown"
        )
        row["summary"] = fence_untrusted_text(
            row["summary"], source="clingen", record_id=record_id
        ).model_dump(mode="json")
    elif domain == "cspec":
        if "description" in row:  # a bare CriteriaCode row (get_cspec_criterion)
            row = _fence_cspec_criterion(row)
        criteria = row.get("criteria")
        if isinstance(criteria, list):  # a CspecDetail row (get_cspec)
            row["criteria"] = [
                _fence_cspec_criterion(c) if isinstance(c, dict) else c for c in criteria
            ]
    return row


def _collect_untrusted_text(value: Any, out: list[dict[str, Any]]) -> None:
    """Recursively collect every fenced ``kind: untrusted_text`` dict inside ``value``."""
    if isinstance(value, dict):
        if value.get("kind") == "untrusted_text":
            out.append(value)
            return
        for v in value.values():
            _collect_untrusted_text(v, out)
    elif isinstance(value, list):
        for item in value:
            _collect_untrusted_text(item, out)


def collect_fenced_objects(*payloads: Any) -> list[UntrustedText]:
    """Recursively find every fenced v1.1 object across the given response payload(s).

    Each tool calls this on exactly what it is about to return (records / record / a
    nested dict), then passes the result to
    :func:`clingen_link.mcp.untrusted_content.enforce_untrusted_text_limits` -- so the
    limit check covers a fenced object no matter which field or how deeply nested (a list
    of records, or the cspec criteria/strengths tree) it ended up in.
    """
    found: list[dict[str, Any]] = []
    for payload in payloads:
        _collect_untrusted_text(payload, found)
    return [UntrustedText.model_validate(d) for d in found]


def shape_record(
    model: BaseModel | dict[str, Any], *, domain: str, response_mode: ResponseMode
) -> dict[str, Any]:
    """Project one domain record according to ``response_mode``.

    ``permalink`` and ``recommended_citation`` are always preserved (the citation
    contract); ``compact`` drops nulls + the domain's verbose fields; ``standard``
    keeps every field but drops the verbose blocks; ``full`` keeps everything. Prose
    fields are fenced (Response-Envelope v1.1) before any mode-based trimming so the
    trimming logic keeps working unchanged on plain dict keys.
    """
    row = _dump(model)
    row = _fence_domain_fields(row, domain)
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
