"""Shared helpers for building the success ``_meta`` block on clingen-link tools.

Every successful tool response carries the same provenance contract: a
``data_version`` + ``fetched_at`` pulled from the snapshot ``meta`` rows,
``next_commands`` (ready-to-call follow-ups), a verbatim ``recommended_citation``
(from the service models), and ``unsafe_for_clinical_use``. Centralising the
assembly keeps the contract identical across the domain tools and out of each
600-LOC tool module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from clingen_link.config import settings

# Per-domain → the snapshot meta-table key whose row carries the freshness signal
# surfaced as data_version. The gene hub spans all four, so it folds them.
_DOMAIN_META_KEY: dict[str, str] = {
    "validity": "validity",
    "dosage": "dosage",
    "actionability": "actionability",
    "erepo": "erepo",
    "cspec": "cspec",
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


def pagination(*, total: int, page: int, size: int, shown: int) -> dict[str, Any]:
    """The Response-Envelope v1 ``_meta.pagination`` block.

    ``has_more`` is the load-bearing field: a partial page that does not say so lets the
    model conclude it has seen everything. It is derived from the counts rather than passed
    in, so it cannot disagree with them (issue #46: a `truncated` block was emitted with no
    boolean anywhere for a client to branch on).
    """
    return {
        "total_count": total,
        "page": page,
        "size": size,
        "returned": shown,
        "has_more": (page - 1) * size + shown < total,
    }


# Collection keys a tool body may use; the envelope publishes them as `results`.
_COLLECTION_KEYS = ("records", "candidates", "expert_panels")


def primary_payload(payload: dict[str, Any]) -> None:
    """Put the payload under `results` (array) or `result` (object), v1 section 1."""
    if isinstance(payload.get("results"), list) or isinstance(payload.get("result"), dict):
        return
    for key in _COLLECTION_KEYS:
        if isinstance(payload.get(key), list):
            payload["results"] = payload.pop(key)
            return
    body = {k: payload.pop(k) for k in list(payload) if k not in ("success", "_meta")}
    payload["result"] = body


def utc_z(value: datetime) -> str:
    """RFC 3339 UTC with ``Z`` (Response-Envelope Standard v1.2 ``retrieved_at``)."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _retrieved_at(fetched_at: str | None, data_version: dict[str, Any]) -> str:
    """Oldest upstream fetch behind a result: the live fetch, else the snapshot download."""
    rows = [data_version] if "fetched_at" in data_version else list(data_version.values())
    stamps = [fetched_at] if fetched_at else [r.get("fetched_at") for r in rows]
    parsed = []
    for stamp in stamps:
        try:
            when = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        parsed.append(when if when.tzinfo else when.replace(tzinfo=UTC))
    # A snapshot without freshness rows has no recorded download time: report the read.
    return utc_z(min(parsed, default=datetime.now(UTC)))


def build_meta(
    *,
    data_version: dict[str, Any],
    next_commands: list[dict[str, Any]],
    record_count: int | None = None,
    truncated: dict[str, Any] | None = None,
    fetched_at: str | None = None,
    pagination_block: dict[str, Any] | None = None,
    live: bool = False,
) -> dict[str, Any]:
    """Assemble the canonical success ``_meta`` block.

    ``unsafe_for_clinical_use`` is injected by ``run_mcp_tool`` for every response, so it is
    omitted here. The ``recommended_citation`` is deliberately NOT emitted in ``_meta``: the
    load-bearing copies live per-record (and as the single top-level summary citation on
    detail/hub tools), so re-stating it in the envelope was pure duplication (assessment M4).
    """
    # Provenance (Response-Envelope Standard v1.2). ClinGen names no release, so
    # data_version is the tag of the snapshot served, or `live` for a live fetch
    # (``live=True`` with its ``fetched_at``). The per-domain freshness rows that
    # were ``_meta.data_version`` before v1.2 are ``_meta.source_versions``.
    meta: dict[str, Any] = {
        "source": "clingen",
        "data_version": "live" if live else settings.data_release_tag,
        "retrieved_at": _retrieved_at(fetched_at, data_version),
        "source_versions": (
            {data_version["domain"]: data_version} if "domain" in data_version else data_version
        ),
        "next_commands": next_commands,
    }
    if fetched_at is not None:
        meta["fetched_at"] = fetched_at
    elif isinstance(data_version, dict) and data_version.get("fetched_at"):
        meta["fetched_at"] = data_version["fetched_at"]
    if record_count is not None:
        meta["record_count"] = record_count
    if pagination_block is not None:
        meta["pagination"] = pagination_block
    if truncated is not None:
        meta["truncated"] = truncated
    return meta
