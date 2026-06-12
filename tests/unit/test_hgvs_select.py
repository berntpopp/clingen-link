"""Tests for canonical HGVS selection (assessment M2)."""

from __future__ import annotations

from clingen_link.mcp.hgvs_select import canonical_hgvs


def test_picks_genomic_mane_protein() -> None:
    hgvs = [
        "NC_000017.11:g.43045761A>C",  # GRCh38 genomic
        "NC_000017.10:g.41197693A>C",  # GRCh37 genomic
        "NM_007294.4:c.5509T>G",  # coding / MANE
        "ENST00000357654.9:c.5509T>G",  # alt transcript
        "NP_009225.1:p.Cys1837Gly",  # protein
    ] + [f"NM_0{n}.1:c.{n}A>G" for n in range(40)]
    out = canonical_hgvs(hgvs)
    assert len(out) <= 3
    assert any(h.startswith("NC_000017.11") for h in out)
    assert any(h.startswith("NM_") and ":c." in h for h in out)
    assert any(":p." in h or h.startswith("NP_") for h in out)


def test_prefers_grch38_genomic_over_grch37() -> None:
    # With coding + protein present, all three slots fill from distinct categories — the GRCh37
    # genomic is not backfilled.
    hgvs = [
        "NC_000017.10:g.1A>C",
        "NC_000017.11:g.2A>C",
        "NM_1.1:c.1A>C",
        "NP_1.1:p.X1Y",
    ] + [f"x{i}" for i in range(3)]
    out = canonical_hgvs(hgvs)
    assert "NC_000017.11:g.2A>C" in out
    assert "NC_000017.10:g.1A>C" not in out


def test_short_list_passthrough() -> None:
    assert canonical_hgvs(["NM_007294.4:c.68_69del"]) == ["NM_007294.4:c.68_69del"]
    assert canonical_hgvs([]) == []
    three = ["a", "b", "c"]
    assert canonical_hgvs(three) == three


def test_backfills_when_categories_missing() -> None:
    # No NC_/NM_/NP_ matches → backfill from the head, still capped at 3.
    out = canonical_hgvs(["a", "b", "c", "d", "e"])
    assert out == ["a", "b", "c"]
