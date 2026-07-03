"""Schema + provenance invariants for the clingen://guidance manifest data."""

from __future__ import annotations

import json
from importlib.resources import files

# NC/ND/none values are reserved for future entries where the paper is not
# openly redistributable or genuinely has no associated journal paper.
OA_LICENSE = {
    "CC-BY-4.0",
    "CC-BY-NC-4.0",  # reserved: non-commercial CC license
    "CC-BY-NC-ND-4.0",  # reserved: non-commercial + no-derivatives CC license
    "author-manuscript",
    "copyright-restricted",  # in PMC, free to read, but all-rights-reserved — no CC license
    "not-in-pmc",
    "none",  # reserved: no associated journal paper (web-only rec)
    "unverified",
}
FULLTEXT = {"redistributable", "read-only", "unavailable", "unverified"}
CATEGORY = {"general", "criteria-specific", "endorsed"}


def _manifest() -> dict:
    raw = files("clingen_link.data").joinpath("svi_guidance.json").read_text(encoding="utf-8")
    return json.loads(raw)


def test_top_level_shape() -> None:
    m = _manifest()
    assert m["source_index"].startswith("https://clinicalgenome.org/")
    assert isinstance(m["recommendations"], list) and m["recommendations"]
    assert m["unsafe_for_clinical_use"] is True
    assert m["research_use_notice"]
    assert m["baseline"]["pmid"] == "25741868"  # ACMG/AMP 2015 baseline


def test_entry_invariants() -> None:
    m = _manifest()
    ids = [e["id"] for e in m["recommendations"]]
    assert len(ids) == len(set(ids)), "entry ids must be unique"
    for e in m["recommendations"]:
        assert e["category"] in CATEGORY, e["id"]
        assert isinstance(e["codes"], list), e["id"]
        assert e["clingen_doc_url"].startswith("https://clinicalgenome.org/docs/"), e["id"]
        assert e["oa_license"] in OA_LICENSE, e["id"]
        assert e["fulltext"] in FULLTEXT, e["id"]
        assert e["pmid"] is None or e["pmid"].isdigit(), e["id"]
        assert e["pmcid"] is None or e["pmcid"].startswith("PMC"), e["id"]
        assert isinstance(e.get("fulltext_access"), str) and e["fulltext_access"], e["id"]
        assert isinstance(e.get("artifacts"), list), e["id"]
        # redistributable <-> CC BY: the pairing that gates fulltext reuse. This
        # guards against mis-tagging a copyright-restricted paper as shareable.
        if e["fulltext"] == "redistributable":
            assert e["oa_license"] == "CC-BY-4.0", e["id"]
        if e["oa_license"] == "CC-BY-4.0":
            assert e["fulltext"] == "redistributable", e["id"]


def test_verified_cc_by_entries_tagged() -> None:
    """Every confirmed CC BY paper must be tagged redistributable."""
    m = {e["id"]: e for e in _manifest()["recommendations"]}
    for slug in ("pp3-bp4-calibration", "ps3-bs3-functional", "pp1-bs4-pp4", "non-coding"):
        assert m[slug]["oa_license"] == "CC-BY-4.0", slug
        assert m[slug]["fulltext"] == "redistributable", slug


def test_no_unverified_entries_ship() -> None:
    """Tier 1 ships fully verified: no oa_license/fulltext left as 'unverified'."""
    m = _manifest()
    for e in m["recommendations"]:
        assert e["oa_license"] != "unverified", e["id"]
        assert e["fulltext"] != "unverified", e["id"]
