"""Per-domain freshness / change-detection signals (spec section 2.2).

Each ``*_signal`` returns a uniform dict::

    {signal_type, signal_value, content_sha256, record_count}

``signal_value`` is the cheapest human-meaningful change marker for the domain
(max date, top release version, ...). ``content_sha256`` is a deterministic,
reorder-stable hash of the canonical key fields — banner / volatile fields are
deliberately excluded so daily regeneration does not produce false "updated"
positives. These four values are persisted into the ``meta`` table by the build
step and compared by ``refresh --check``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any


def _rfc1123_sort_key(value: str) -> datetime:
    """Chronological sort key for an RFC1123 datetime string.

    Actionability ``lastUpdated`` values look like ``Wed, 20 May 2026 17:53:34
    -0000``. String comparison sorts them by day-of-month first, so a plain
    ``max()`` is wrong (e.g. ``30 Mar 2022`` > ``20 May 2026`` lexically). Parse
    to an aware ``datetime`` instead; unparseable values sort to the epoch.
    """
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def sha256_rows(rows: list[dict[str, Any]], key_fields: list[str]) -> str:
    """Return a deterministic sha256 over ``rows`` projected onto ``key_fields``.

    Each row is reduced to a tuple of its ``key_fields`` values (stringified),
    the tuples are sorted, and the canonical JSON of the sorted list is hashed.
    This makes the digest independent of input row order and of any field not in
    ``key_fields`` (banners, volatile timestamps, etc.).
    """
    projected: list[list[str]] = []
    for row in rows:
        projected.append(["" if row.get(f) is None else str(row.get(f)) for f in key_fields])
    projected.sort()
    canonical = json.dumps(projected, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_text(text: str) -> str:
    """Return the sha256 hex digest of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _max_value(rows: list[dict[str, Any]], field: str) -> str:
    """Return the lexical max non-empty value of ``field`` across rows ("" if none)."""
    values = [str(row[field]) for row in rows if row.get(field)]
    return max(values) if values else ""


def validity_signal(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Freshness signal for gene-disease validity.

    ``signal_value`` is the max ISO ``classified_date``. The hash covers the
    stable per-record identity ``(perm_id, classification, classified_date)``;
    the JSON API has no daily banner so this is stable across pulls.
    """
    return {
        "signal_type": "max_classified_date",
        "signal_value": _max_value(rows, "classified_date"),
        "content_sha256": sha256_rows(rows, ["perm_id", "classification", "classified_date"]),
        "record_count": len(rows),
    }


def dosage_signal(etags: dict[str, str]) -> dict[str, Any]:
    """Freshness signal for gene dosage.

    The FTP files support conditional GET, so the cheapest signal is the set of
    per-file ``ETag`` / ``Last-Modified`` values captured at fetch time. The
    signal value is a sorted, joined ``file=etag`` string and the hash is its
    digest.
    """
    parts = sorted(f"{name}={value}" for name, value in etags.items())
    joined = ";".join(parts)
    return {
        "signal_type": "etags",
        "signal_value": joined,
        "content_sha256": _sha256_text(joined),
        "record_count": len(etags),
    }


def actionability_signal(brief: list[dict[str, Any]]) -> dict[str, Any]:
    """Freshness signal for clinical actionability.

    ``signal_value`` is the max ``metadata.lastUpdated``. The hash covers
    ``(docId, release, lastUpdated)`` per the spec, derived from the raw brief
    so it is independent of downstream parsing.
    """
    projected: list[dict[str, Any]] = []
    last_updated_values: list[str] = []
    for doc in brief:
        doc_id = doc.get("docId")
        metadata = doc.get("metadata") or {}
        last_updated = metadata.get("lastUpdated") or ""
        if last_updated:
            last_updated_values.append(str(last_updated))
        context = doc.get("context") or {}
        release_parts: list[str] = []
        for ctx_name in ("Adult", "Pediatric"):
            ctx = context.get(ctx_name) or {}
            release = ctx.get("release") or {}
            release_parts.append(str(release.get("number") or release.get("date") or ""))
        projected.append(
            {
                "doc_id": str(doc_id or ""),
                "release": "|".join(release_parts),
                "last_updated": str(last_updated),
            }
        )
    signal_value = max(last_updated_values, key=_rfc1123_sort_key) if last_updated_values else ""
    return {
        "signal_type": "max_last_updated",
        "signal_value": signal_value,
        "content_sha256": sha256_rows(projected, ["doc_id", "release", "last_updated"]),
        "record_count": len(brief),
    }


def _top_related_version(news: list[dict[str, Any]]) -> str:
    """Return the ``relatedVersion`` of the most recent ERepo news entry."""
    best_date = ""
    best_version = ""
    for entry in news:
        date = str(entry.get("date") or "")
        version = str(entry.get("relatedVersion") or "")
        if version and date >= best_date:
            best_date = date
            best_version = version
    return best_version


def erepo_signal(news: list[dict[str, Any]], tsv_text: str) -> dict[str, Any]:
    """Freshness signal for ERepo variant pathogenicity.

    ``signal_value`` is the top ``relatedVersion`` from the news feed. The hash
    covers ``(Uuid, Approval Date, Retracted)`` tuples extracted directly from
    the bulk TSV — cheap and stable regardless of row order.
    """
    import csv
    import io

    reader = csv.reader(io.StringIO(tsv_text), delimiter="\t")
    rows = list(reader)
    projected: list[dict[str, str]] = []
    if rows and rows[0] and rows[0][0] == "Variation":
        rows = rows[1:]
    for cells in rows:
        if len(cells) < 20:
            continue
        projected.append(
            {
                "uuid": cells[19],
                "approval_date": cells[15],
                "retracted": cells[17],
            }
        )
    return {
        "signal_type": "related_version",
        "signal_value": _top_related_version(news),
        "content_sha256": sha256_rows(projected, ["uuid", "approval_date", "retracted"]),
        "record_count": len(projected),
    }


def cspec_signal(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """Freshness signal for the CSpec registry.

    Cheap (one catalog list call): the published-candidate count is the value and
    the hash covers ``(entId, criteriaCode_count, ruleSet_count)`` per spec, so
    additions, criteria changes, and rule-set changes all flip the digest without
    fetching any per-spec document.
    """
    projected: list[dict[str, str]] = []
    published = 0
    for row in catalog:
        ent_id = str(row.get("entId") or "")
        ld = row.get("ld") or {}
        cc = int(ld.get("CriteriaCode") or 0)
        rs = int(ld.get("RuleSet") or 0)
        if cc > 0:
            published += 1
        projected.append({"ent_id": ent_id, "cc": str(cc), "rs": str(rs)})
    return {
        "signal_type": "published_count",
        "signal_value": str(published),
        "content_sha256": sha256_rows(projected, ["ent_id", "cc", "rs"]),
        "record_count": published,
    }
