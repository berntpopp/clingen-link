"""Fail-closed, deterministic identity for an immutable ClinGen data release."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast


class ReleaseIdentityError(ValueError):
    """A release manifest is not a complete, typed sealed identity."""


ReleaseState = Literal["create", "published_noop", "draft_publish_existing", "collision"]
_HEX = frozenset("0123456789abcdef")
MAX_MANIFEST_BYTES = 1 << 20
RELEASE_ASSETS = frozenset({"clingen.sqlite.zst", "data-release-manifest.json", "SHA256SUMS"})
_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "dataset",
        "schema",
        "record_counts",
        "artifact",
        "previous_known_good_digest",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {
        "filename",
        "sha256",
        "compressed_size",
        "max_compressed_size",
        "expanded_tree_sha256",
        "expanded_size",
        "max_expanded_size",
        "member_count",
        "max_members",
    }
)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ReleaseIdentityError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseIdentityError(f"{name} must be a non-empty string")
    return value


def _sha(value: object, name: str) -> str:
    text = _text(value, name)
    if len(text) != 64 or any(char not in _HEX for char in text):
        raise ReleaseIdentityError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReleaseIdentityError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class ReleaseIdentity:
    """Only stable manifest fields; capture/build timestamps are intentionally absent."""

    tag: str
    source_identifier: str
    source_url: str
    source_sha256: str
    snapshot_schema: str
    record_counts: tuple[tuple[str, int], ...]
    artifact_filename: str
    artifact_sha256: str
    compressed_size: int
    expanded_tree_sha256: str
    expanded_size: int
    member_count: int


def sealed_identity(manifest: object) -> ReleaseIdentity:
    """Extract a typed identity from a decoded manifest, rejecting ambiguity."""
    root = _mapping(manifest, "manifest")
    if set(root) != _ROOT_FIELDS or root.get("schema_version") != 1:
        raise ReleaseIdentityError("schema_version must be integer 1")
    dataset = _mapping(root.get("dataset"), "dataset")
    source = _mapping(dataset.get("source"), "dataset.source")
    schema = _mapping(root.get("schema"), "schema")
    artifact = _mapping(root.get("artifact"), "artifact")
    if set(dataset) != {"name", "release", "source"} or set(source) not in (
        {"identifier", "url", "sha256"},
        {"identifier", "url", "sha256", "retrieved_at"},
    ):
        raise ReleaseIdentityError("dataset/source shape is not exact")
    if set(schema) != {"minimum", "maximum", "actual"}:
        raise ReleaseIdentityError("schema shape is not exact")
    if set(artifact) != _ARTIFACT_FIELDS or artifact.get("filename") != "clingen.sqlite.zst":
        raise ReleaseIdentityError("artifact shape or filename is not exact")
    counts = _mapping(root.get("record_counts"), "record_counts")
    parsed_counts: list[tuple[str, int]] = []
    for name, count in counts.items():
        parsed_counts.append(
            (_text(name, "record_counts key"), _positive_int(count, f"record_counts.{name}"))
        )
    if not parsed_counts:
        raise ReleaseIdentityError("record_counts must not be empty")
    source_sha = _sha(source.get("sha256"), "dataset.source.sha256")
    if not _text(source.get("url"), "dataset.source.url").startswith("https://"):
        raise ReleaseIdentityError("dataset.source.url must be HTTPS")
    actual = _text(schema.get("actual"), "schema.actual")
    if any(not value.isdigit() for value in actual.split(".")) or len(actual.split(".")) != 3:
        raise ReleaseIdentityError("schema.actual must be a semantic version")
    if any(
        _text(schema.get(name), f"schema.{name}").split(".")[0] != actual.split(".")[0]
        for name in ("minimum", "maximum")
    ):
        raise ReleaseIdentityError("schema bounds must share the actual major")
    if (
        _positive_int(artifact.get("compressed_size"), "artifact.compressed_size")
        > _positive_int(artifact.get("max_compressed_size"), "artifact.max_compressed_size")
        or _positive_int(artifact.get("expanded_size"), "artifact.expanded_size")
        > _positive_int(artifact.get("max_expanded_size"), "artifact.max_expanded_size")
        or _positive_int(artifact.get("member_count"), "artifact.member_count")
        > _positive_int(artifact.get("max_members"), "artifact.max_members")
    ):
        raise ReleaseIdentityError("artifact exceeds declared bounds")
    if _text(dataset.get("release"), "dataset.release") != f"data-clingen-{source_sha[:16]}":
        raise ReleaseIdentityError("dataset.release does not match source identity")
    return ReleaseIdentity(
        tag=f"data-clingen-{source_sha[:16]}",
        source_identifier=_text(source.get("identifier"), "dataset.source.identifier"),
        source_url=_text(source.get("url"), "dataset.source.url"),
        source_sha256=source_sha,
        snapshot_schema=actual,
        record_counts=tuple(sorted(parsed_counts)),
        artifact_filename=_text(artifact.get("filename"), "artifact.filename"),
        artifact_sha256=_sha(artifact.get("sha256"), "artifact.sha256"),
        compressed_size=_positive_int(artifact.get("compressed_size"), "artifact.compressed_size"),
        expanded_tree_sha256=_sha(
            artifact.get("expanded_tree_sha256"), "artifact.expanded_tree_sha256"
        ),
        expanded_size=_positive_int(artifact.get("expanded_size"), "artifact.expanded_size"),
        member_count=_positive_int(artifact.get("member_count"), "artifact.member_count"),
    )


def release_state(
    candidate: ReleaseIdentity, existing: ReleaseIdentity | None, is_draft: bool | None
) -> ReleaseState:
    """Return the only allowed immutable transition; callers never delete or replace."""
    if existing is None:
        if is_draft is not None:
            raise ReleaseIdentityError("release state is ambiguous")
        return "create"
    if is_draft is None:
        raise ReleaseIdentityError("existing release must declare draft state")
    if existing != candidate:
        return "collision"
    return "draft_publish_existing" if is_draft else "published_noop"


def load_sealed_identity(manifest_path: Path) -> ReleaseIdentity:
    """Read a regular manifest through a no-follow descriptor under a 1 MiB cap."""
    status = manifest_path.lstat()
    if not manifest_path.is_file() or status.st_size > MAX_MANIFEST_BYTES:
        raise ReleaseIdentityError("manifest must be a regular file no larger than 1 MiB")
    try:
        descriptor = os.open(manifest_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ReleaseIdentityError("manifest cannot be opened without following links") from error
    try:
        with os.fdopen(descriptor, "rb") as source:
            payload = source.read(MAX_MANIFEST_BYTES + 1)
    except OSError as error:
        raise ReleaseIdentityError("manifest read failed") from error
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ReleaseIdentityError("manifest exceeds 1 MiB")
    try:
        return sealed_identity(json.loads(payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseIdentityError("manifest is not valid JSON") from error


def assert_exact_assets(asset_names: object) -> None:
    """Reject missing, extra, duplicate, or non-string release asset names."""
    if not isinstance(asset_names, list) or any(not isinstance(name, str) for name in asset_names):
        raise ReleaseIdentityError("release assets must be a JSON string list")
    if len(asset_names) != len(set(asset_names)) or set(asset_names) != RELEASE_ASSETS:
        raise ReleaseIdentityError("release asset inventory is not exact")


def parse_sha256sums(payload: str, expected_names: set[str]) -> dict[str, str]:
    """Parse an exact, filename-keyed SHA256SUMS handoff.

    The checksum convention is ``<digest>  <filename>``.  Parsing into a
    filename-keyed mapping keeps validation aligned with the later file lookup
    and rejects missing, extra, duplicate, malformed, or non-canonical records.
    """
    if (
        not isinstance(payload, str)
        or not expected_names
        or any(not isinstance(name, str) or not name for name in expected_names)
    ):
        raise ReleaseIdentityError("SHA256SUMS input is not a non-empty file set")
    lines = payload.splitlines()
    if len(lines) != len(expected_names):
        raise ReleaseIdentityError("SHA256SUMS record count is not exact")
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        fields = line.split()
        if len(fields) != 2:
            raise ReleaseIdentityError(f"SHA256SUMS line {line_number} is malformed")
        digest, name = fields
        if name not in expected_names:
            raise ReleaseIdentityError(f"SHA256SUMS line {line_number} names an unexpected file")
        if name in checksums:
            raise ReleaseIdentityError(f"SHA256SUMS line {line_number} duplicates a file")
        if len(digest) != 64 or any(char not in _HEX for char in digest):
            raise ReleaseIdentityError(f"SHA256SUMS line {line_number} has an invalid digest")
        checksums[name] = digest
    if set(checksums) != expected_names:
        raise ReleaseIdentityError("SHA256SUMS filenames are not exact")
    return checksums


def assert_exact_asset_metadata(assets: object) -> None:
    """Validate the projected GitHub metadata before an asset ID reaches a URL.

    Callers must project API records to exactly ``name``, ``id``, ``size``, and
    ``digest`` first.  That makes the policy independent of mutable or
    undocumented API fields while rejecting duplicate, string, zero, or
    negative IDs before any request interpolates one.
    """
    if not isinstance(assets, list):
        raise ReleaseIdentityError("release assets must be a JSON list")
    names: list[str] = []
    identifiers: list[int] = []
    for index, value in enumerate(assets):
        asset = _mapping(value, f"release asset {index}")
        if set(asset) != {"name", "id", "size", "digest"}:
            raise ReleaseIdentityError("release asset metadata shape is not exact")
        names.append(_text(asset.get("name"), f"release asset {index}.name"))
        identifier = asset.get("id")
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
            raise ReleaseIdentityError("release asset ID must be a positive integer")
        identifiers.append(identifier)
        _positive_int(asset.get("size"), f"release asset {index}.size")
        digest = _text(asset.get("digest"), f"release asset {index}.digest")
        if not digest.startswith("sha256:"):
            raise ReleaseIdentityError("release asset digest must have sha256 prefix")
        _sha(digest.removeprefix("sha256:"), f"release asset {index}.digest")
    assert_exact_assets(names)
    if len(identifiers) != len(set(identifiers)):
        raise ReleaseIdentityError("release asset IDs must be unique")
