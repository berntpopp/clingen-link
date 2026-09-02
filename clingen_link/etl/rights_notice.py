"""The committed, versioned redistribution rights notice for the ClinGen snapshot.

ClinGen's curated data is CC BY 4.0, so redistribution needs honest attribution
rather than a per-release human sign-off.  The notice lives in ``data/RIGHTS.json``
under version control; the data-release workflow validates its presence and shape
and copies it verbatim into the published, attested release manifest.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

DEFAULT_RIGHTS_PATH = Path(__file__).resolve().parents[2] / "data" / "RIGHTS.json"
MAX_RIGHTS_BYTES = 1 << 16

_FIELDS = frozenset(
    {
        "schema_version",
        "dataset",
        "license",
        "attribution",
        "citation",
        "source_url",
        "terms_url",
        "terms_reviewed_at",
    }
)
_LICENSE_FIELDS = frozenset({"name", "spdx_id", "url"})


class RightsNoticeError(ValueError):
    """The committed rights notice is missing, malformed, or incomplete."""


@dataclass(frozen=True)
class RightsNotice:
    """A validated notice plus the canonical block published in the manifest."""

    digest: str
    license_name: str
    license_url: str
    attribution: str
    terms_url: str
    terms_reviewed_at: str
    block: Mapping[str, object]


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RightsNoticeError(f"{field} must be a non-empty string")
    return value


def _https(record: Mapping[str, object], field: str) -> str:
    value = _text(record, field)
    if not value.startswith("https://"):
        raise RightsNoticeError(f"{field} must be an HTTPS URL")
    return value


def validate_rights_notice(raw: object, *, today: date | None = None) -> RightsNotice:
    """Validate a decoded rights notice for exact shape; never for staleness.

    A permissive licence does not expire between releases, so the only temporal
    check is that the review date is not in the future.
    """
    if not isinstance(raw, dict) or set(raw) != _FIELDS:
        raise RightsNoticeError("rights notice must contain exactly the required fields")
    record = cast(Mapping[str, object], raw)
    if record.get("schema_version") != 1:
        raise RightsNoticeError("rights notice schema_version must be integer 1")
    licence = record.get("license")
    if not isinstance(licence, dict) or set(licence) != _LICENSE_FIELDS:
        raise RightsNoticeError("license must name exactly name, spdx_id, and url")
    licence_map = cast(Mapping[str, object], licence)
    name = _text(licence_map, "name")
    _text(licence_map, "spdx_id")
    url = _https(licence_map, "url")
    attribution = _text(record, "attribution")
    _text(record, "citation")
    _text(record, "dataset")
    _https(record, "source_url")
    terms_url = _https(record, "terms_url")
    reviewed_text = _text(record, "terms_reviewed_at")
    try:
        reviewed = date.fromisoformat(reviewed_text)
    except ValueError as error:
        raise RightsNoticeError("terms_reviewed_at must be an ISO-8601 date") from error
    if reviewed > (today or datetime.now(UTC).date()):
        raise RightsNoticeError("terms_reviewed_at must not be in the future")
    block = json.loads(json.dumps(dict(record), sort_keys=True))
    canonical = json.dumps(block, sort_keys=True, separators=(",", ":")).encode()
    return RightsNotice(
        digest="sha256:" + sha256(canonical).hexdigest(),
        license_name=name,
        license_url=url,
        attribution=attribution,
        terms_url=terms_url,
        terms_reviewed_at=reviewed.isoformat(),
        block=block,
    )


def load_rights_notice(path: Path | None = None, *, today: date | None = None) -> RightsNotice:
    """Read the committed notice through a bounded, no-follow descriptor."""
    source = DEFAULT_RIGHTS_PATH if path is None else path
    try:
        status = source.lstat()
        regular = source.is_file()
    except OSError as error:
        raise RightsNoticeError("rights notice is missing") from error
    if not regular or status.st_size > MAX_RIGHTS_BYTES:
        raise RightsNoticeError("rights notice must be a regular file within the size limit")
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            payload = handle.read(MAX_RIGHTS_BYTES + 1)
    except OSError as error:
        raise RightsNoticeError("rights notice cannot be read without following links") from error
    if len(payload) > MAX_RIGHTS_BYTES:
        raise RightsNoticeError("rights notice exceeds the size limit")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RightsNoticeError("rights notice is not valid JSON") from error
    return validate_rights_notice(decoded, today=today)
