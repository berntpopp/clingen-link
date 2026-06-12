"""Unit tests for ``cspec_signal`` — the CSpec registry freshness signal.

The signal runs inside ``populate()`` with no per-domain isolation, so it must
degrade gracefully on malformed catalog rows rather than raise and abort the
whole snapshot build (consistent with every other ``*_signal``).
"""

from clingen_link.etl import freshness


def test_cspec_signal_published_count_and_keys() -> None:
    catalog = [
        {"entId": "GN001", "ld": {"CriteriaCode": 3, "RuleSet": 1}},
        {"entId": "GN002", "ld": {"CriteriaCode": 5, "RuleSet": 2}},
        {"entId": "GN003", "ld": {"CriteriaCode": 0, "RuleSet": 0}},
    ]
    signal = freshness.cspec_signal(catalog)
    assert set(signal) == {"signal_type", "signal_value", "content_sha256", "record_count"}
    assert signal["signal_type"] == "published_count"
    # Two rows have CriteriaCode > 0.
    assert signal["signal_value"] == "2"
    assert signal["record_count"] == 2


def test_cspec_signal_string_numeric_criteria_code_counts() -> None:
    catalog = [
        {"entId": "GN001", "ld": {"CriteriaCode": "3", "RuleSet": "1"}},
    ]
    signal = freshness.cspec_signal(catalog)
    assert signal["record_count"] == 1
    assert signal["signal_value"] == "1"


def test_cspec_signal_missing_ld_does_not_raise() -> None:
    catalog = [
        {"entId": "GN001"},  # no ld at all
        {"entId": "GN002", "ld": None},  # explicit None
        {"entId": "GN003", "ld": {"CriteriaCode": 4, "RuleSet": 1}},
    ]
    signal = freshness.cspec_signal(catalog)
    # Only the well-formed row with CriteriaCode > 0 counts.
    assert signal["record_count"] == 1
    assert signal["signal_value"] == "1"


def test_cspec_signal_malformed_rows_treated_as_zero() -> None:
    catalog = [
        {"entId": "GN001", "ld": {"CriteriaCode": "N/A", "RuleSet": "?"}},
        {"entId": "GN002", "ld": ["not", "a", "dict"]},  # ld is a list
        {"entId": "GN003", "ld": {"CriteriaCode": None, "RuleSet": None}},
        {"entId": "GN004", "ld": {"CriteriaCode": 2, "RuleSet": 1}},
    ]
    # Must not raise on any malformed row.
    signal = freshness.cspec_signal(catalog)
    # Only GN004 has a coercible CriteriaCode > 0.
    assert signal["record_count"] == 1
    assert signal["signal_value"] == "1"
    assert len(signal["content_sha256"]) == 64


def test_cspec_signal_empty_catalog() -> None:
    signal = freshness.cspec_signal([])
    assert signal["record_count"] == 0
    assert signal["signal_value"] == "0"
