"""Tests for HGNC complete-set ingestion (assessment L2/L3)."""

from __future__ import annotations

from clingen_link.etl import hgnc

_TSV = (
    "hgnc_id\tsymbol\tname\talias_symbol\tprev_symbol\n"
    "HGNC:1101\tBRCA2\tBRCA2 DNA repair associated\tFACD|FANCD1\tFANCD1\n"
    "HGNC:1100\tBRCA1\tBRCA1 DNA repair associated\tRNF53\t\n"
    "HGNC:0\t\tempty symbol row\tX\tY\n"
)


def test_parse_hgnc_extracts_name_and_aliases() -> None:
    rows = hgnc.parse_hgnc(_TSV)
    by_symbol = {r["symbol"]: r for r in rows}
    assert "BRCA2" in by_symbol
    assert by_symbol["BRCA2"]["name"] == "BRCA2 DNA repair associated"
    # alias_symbol + prev_symbol merged, deduped, pipe-split.
    assert by_symbol["BRCA2"]["aliases"] == ["FACD", "FANCD1"]
    assert by_symbol["BRCA1"]["hgnc_id"] == "HGNC:1100"
    # Blank-symbol rows are skipped.
    assert "" not in by_symbol


def test_index_by_symbol() -> None:
    idx = hgnc.index_by_symbol(hgnc.parse_hgnc(_TSV))
    assert idx["BRCA1"]["name"].startswith("BRCA1")
