"""Strict, digest-bound authorization records for ClinGen redistribution."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from typing import cast

from clingen_link.etl.release_identity import ReleaseIdentity


class RightsRecordError(ValueError):
    """The protected rights record is incomplete, stale, or not handoff-bound."""


_FIELDS = frozenset(
    {
        "schema_version",
        "decision",
        "decision_at",
        "terms_reviewed_at",
        "permitted_use",
        "attribution",
        "evidence_uri",
        "reviewer",
        "authority",
        "tag",
        "source_sha256",
        "artifact_sha256",
    }
)
_USE = "immutable ClinGen reference snapshot redistribution"


@dataclass(frozen=True)
class RightsRecord:
    digest: str
    reviewer: str
    authority: str
    decision_at: str


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RightsRecordError(f"{field} must be a non-empty string")
    return value


def validate_rights_record(
    raw: object, identity: ReleaseIdentity, *, today: date | None = None
) -> RightsRecord:
    """Validate the sole allowed secret record against the exact sealed handoff."""
    if not isinstance(raw, dict) or set(raw) != _FIELDS:
        raise RightsRecordError("rights record must contain exactly the required fields")
    record = cast(Mapping[str, object], raw)
    if record.get("schema_version") != 1 or record.get("decision") != "affirmative":
        raise RightsRecordError("rights decision must be schema 1 and affirmative")
    if _text(record, "permitted_use") != _USE:
        raise RightsRecordError("permitted_use is not approved")
    if not _text(record, "evidence_uri").startswith("https://"):
        raise RightsRecordError("evidence_uri must be HTTPS")
    try:
        decided = datetime.fromisoformat(_text(record, "decision_at").replace("Z", "+00:00"))
        reviewed = date.fromisoformat(_text(record, "terms_reviewed_at"))
    except ValueError as error:
        raise RightsRecordError("rights dates must be ISO-8601") from error
    now = today or datetime.now(UTC).date()
    if (
        decided.tzinfo is None
        or decided > datetime.now(UTC)
        or reviewed > now
        or now - reviewed > timedelta(days=366)
    ):
        raise RightsRecordError("rights decision/terms review is stale or invalid")
    for field, expected in (
        ("tag", identity.tag),
        ("source_sha256", identity.source_sha256),
        ("artifact_sha256", identity.artifact_sha256),
    ):
        if _text(record, field) != expected:
            raise RightsRecordError(f"{field} does not bind the sealed identity")
    canonical = json.dumps(dict(record), sort_keys=True, separators=(",", ":")).encode()
    return RightsRecord(
        "sha256:" + sha256(canonical).hexdigest(),
        _text(record, "reviewer"),
        _text(record, "authority"),
        decided.isoformat(),
    )
