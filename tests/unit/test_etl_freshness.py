"""Tests for clingen_link.etl.freshness (signal determinism + reorder stability)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clingen_link.etl import freshness, parse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# sha256_rows
# ---------------------------------------------------------------------------


def test_sha256_rows_reorder_stable() -> None:
    rows = [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 3}]
    reordered = [rows[2], rows[0], rows[1]]
    assert freshness.sha256_rows(rows, ["id", "v"]) == freshness.sha256_rows(reordered, ["id", "v"])


def test_sha256_rows_ignores_non_key_fields() -> None:
    a = [{"id": "x", "banner": "2026-06-12"}]
    b = [{"id": "x", "banner": "2026-06-13"}]
    assert freshness.sha256_rows(a, ["id"]) == freshness.sha256_rows(b, ["id"])


def test_sha256_rows_sensitive_to_key_change() -> None:
    a = [{"id": "x"}]
    b = [{"id": "y"}]
    assert freshness.sha256_rows(a, ["id"]) != freshness.sha256_rows(b, ["id"])


def test_sha256_rows_handles_none() -> None:
    rows = [{"id": None}]
    # Must not raise and must be deterministic.
    assert freshness.sha256_rows(rows, ["id"]) == freshness.sha256_rows(rows, ["id"])


# ---------------------------------------------------------------------------
# validity_signal
# ---------------------------------------------------------------------------


def test_validity_signal_max_date_and_reorder() -> None:
    rows = parse.parse_validity(_load_json("validity_api_small.json")["rows"])
    sig = freshness.validity_signal(rows)
    assert sig["signal_type"] == "max_classified_date"
    assert sig["signal_value"] == "2024-09-17T16:00:00.000Z"
    assert sig["record_count"] == 5
    reordered = list(reversed(rows))
    assert freshness.validity_signal(reordered)["content_sha256"] == sig["content_sha256"]


# ---------------------------------------------------------------------------
# dosage_signal
# ---------------------------------------------------------------------------


def test_dosage_signal_from_etags() -> None:
    sig = freshness.dosage_signal({"b.tsv": '"2"', "a.tsv": '"1"'})
    assert sig["signal_type"] == "etags"
    # Sorted by filename regardless of insertion order.
    assert sig["signal_value"] == 'a.tsv="1";b.tsv="2"'
    assert sig["record_count"] == 2
    # Order independence.
    assert (
        freshness.dosage_signal({"a.tsv": '"1"', "b.tsv": '"2"'})["content_sha256"]
        == sig["content_sha256"]
    )


# ---------------------------------------------------------------------------
# actionability_signal
# ---------------------------------------------------------------------------


def test_actionability_signal_max_last_updated() -> None:
    brief = _load_json("actionability_brief_small.json")
    sig = freshness.actionability_signal(brief)
    assert sig["signal_type"] == "max_last_updated"
    assert sig["signal_value"] == "Wed, 20 May 2026 17:53:34 -0000"
    assert sig["record_count"] == 5
    reordered = list(reversed(brief))
    assert freshness.actionability_signal(reordered)["content_sha256"] == sig["content_sha256"]


def test_actionability_signal_uses_chronological_not_string_max() -> None:
    # A recent date with a low day-of-month must beat an old date with a high
    # day-of-month. Plain string max() would wrongly pick "30 Mar 2022".
    brief = [
        {"docId": "AC1", "metadata": {"lastUpdated": "Wed, 30 Mar 2022 20:08:13 -0000"}},
        {"docId": "AC2", "metadata": {"lastUpdated": "Wed, 20 May 2026 17:53:34 -0000"}},
    ]
    sig = freshness.actionability_signal(brief)
    assert sig["signal_value"] == "Wed, 20 May 2026 17:53:34 -0000"


# ---------------------------------------------------------------------------
# erepo_signal
# ---------------------------------------------------------------------------


def test_erepo_signal_version_and_hash() -> None:
    news = _load_json("erepo_news_sample.json")["data"]
    tsv = _read("erepo_bulk.head.tsv")
    sig = freshness.erepo_signal(news, tsv)
    assert sig["signal_type"] == "related_version"
    assert sig["signal_value"] == "2.5.6"
    assert sig["record_count"] == 5
    # Hash is stable for the same TSV content.
    assert freshness.erepo_signal(news, tsv)["content_sha256"] == sig["content_sha256"]


def test_erepo_signal_empty_news() -> None:
    tsv = _read("erepo_bulk.head.tsv")
    sig = freshness.erepo_signal([], tsv)
    assert sig["signal_value"] == ""
    assert sig["record_count"] == 5
