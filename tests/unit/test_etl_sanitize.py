"""Tests for HTML sanitization + obsolescence detection (assessment M1)."""

from __future__ import annotations

from clingen_link.etl import sanitize
from clingen_link.models.models import ValidityAssertion


def test_strip_html_removes_tags_and_collapses_ws() -> None:
    raw = ' familial isolated dilated cardiomyopathy <span class="badge">Obsolete Term</span> '
    assert sanitize.strip_html(raw) == "familial isolated dilated cardiomyopathy Obsolete Term"


def test_strip_html_unescapes_entities() -> None:
    assert (
        sanitize.strip_html("Pendred&nbsp;syndrome &amp; deafness") == "Pendred syndrome & deafness"
    )


def test_strip_html_none_and_blank() -> None:
    assert sanitize.strip_html(None) == ""
    assert sanitize.strip_html("   ") == ""


def test_is_obsolete_label_detects_marker() -> None:
    assert sanitize.is_obsolete_label("x <span>Obsolete Term</span>") is True
    assert sanitize.is_obsolete_label("obsolete glaucoma 1, open angle, F") is True
    assert sanitize.is_obsolete_label("dilated cardiomyopathy") is False
    assert sanitize.is_obsolete_label(None) is False


def test_validity_assertion_keeps_raw_disease_name_for_fencing() -> None:
    # v1.1: disease_name is carried VERBATIM by the model (no strip_html) so the MCP-boundary
    # fence can hash the raw upstream bytes and never regex-delete prose. The obsolescence
    # marker is still surfaced as a structured boolean derived from the raw label, and the
    # (MONDO-referenced) citation never embeds the label.
    raw = 'dilated cardiomyopathy <span class="badge">Obsolete Term</span>'
    row = {
        "symbol": "TMPO",
        "disease_name": raw,
        "perm_id": "p1",
        "mondo": "MONDO:0005045",
        "classification": "Limited",
    }
    model = ValidityAssertion.from_row(row)
    assert model.disease_name == raw  # verbatim, HTML preserved as inert data
    assert model.disease_obsolete is True
    assert "<span" not in model.recommended_citation


def test_validity_assertion_non_obsolete() -> None:
    model = ValidityAssertion.from_row(
        {"symbol": "BRCA1", "disease_name": "hereditary breast cancer", "perm_id": "p2"}
    )
    assert model.disease_obsolete is False
