"""Tests for clingen_link.etl.parse (pure parsers against fixtures)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clingen_link.etl import parse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------


def test_parse_validity_strips_trailing_spaces_and_renames() -> None:
    rows = _load_json("validity_api_small.json")["rows"]
    out = parse.parse_validity(rows)
    first = out[0]
    assert first["symbol"] == "AARS1"
    assert first["disease_name"] == "Charcot-Marie-Tooth disease axonal type 2N"
    assert first["expert_panel"].startswith("Charcot-Marie-Tooth")
    assert first["classified_date"] == "2024-03-14T16:00:00.000Z"
    assert first["mondo"] == "MONDO:0013212"
    assert first["report_id"] == "92de3832-c272-4993-8586-288c6331dec2"
    assert "ep" not in first


def test_parse_validity_count_matches() -> None:
    rows = _load_json("validity_api_small.json")["rows"]
    assert len(parse.parse_validity(rows)) == 5


def test_parse_validity_sanitizes_html_and_flags_obsolete() -> None:
    rows = [
        {
            "symbol": "TMPO",
            "disease_name": 'dilated cardiomyopathy <span class="badge">Obsolete Term</span>',
            "perm_id": "p1",
        },
        {"symbol": "BRCA1", "disease_name": "hereditary breast cancer", "perm_id": "p2"},
    ]
    out = parse.parse_validity(rows)
    assert out[0]["disease_name"] == "dilated cardiomyopathy Obsolete Term"
    assert out[0]["disease_obsolete"] is True
    assert out[1]["disease_obsolete"] is False


# ---------------------------------------------------------------------------
# Dosage
# ---------------------------------------------------------------------------


@pytest.fixture
def dosage_rows() -> list[dict[str, Any]]:
    return parse.parse_dosage(
        _read("dosage_gene_GRCh38.head.tsv"),
        _read("dosage_region_GRCh38.head.tsv"),
    )


def _by_symbol(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    return next(r for r in rows if r.get("symbol") == symbol)


def test_parse_dosage_skips_comment_lines(dosage_rows: list[dict[str, Any]]) -> None:
    # 6 gene rows + 4 region rows in the head fixtures, no comment rows.
    assert len(dosage_rows) == 10
    assert all(not (r.get("symbol") or "").startswith("#") for r in dosage_rows)


def test_parse_dosage_decodes_score_code_30(dosage_rows: list[dict[str, Any]]) -> None:
    row = _by_symbol(dosage_rows, "A4GALT")
    assert row["haplo_score"] == "Gene associated with autosomal recessive phenotype"
    assert row["record_type"] == "gene"
    assert row["cytoband"] == "22q13.2"
    assert row["grch38"] == "chr22:42692121-42721301"


def test_parse_dosage_keeps_ordinal_scores(dosage_rows: list[dict[str, Any]]) -> None:
    aars1 = _by_symbol(dosage_rows, "AARS1")
    assert aars1["haplo_score"] == "0"
    aagab = _by_symbol(dosage_rows, "AAGAB")
    assert aagab["haplo_score"] == "3"


def test_parse_dosage_collects_pmids_and_mondo(dosage_rows: list[dict[str, Any]]) -> None:
    aagab = _by_symbol(dosage_rows, "AAGAB")
    assert aagab["haplo_pmids"] == ["23064416", "23000146"]
    assert aagab["triplo_pmids"] == []
    assert aagab["haplo_mondo"] == "MONDO:0007858"
    assert aagab["triplo_mondo"] is None


def test_parse_dosage_region_records(dosage_rows: list[dict[str, Any]]) -> None:
    regions = [r for r in dosage_rows if r["record_type"] == "region"]
    assert len(regions) == 4
    first = regions[0]
    assert first["isca_id"] == "ISCA-46757"
    assert first["symbol"] is None
    assert first["triplo_score"] == "3"


def test_parse_dosage_grch37_backfill() -> None:
    gene_tsv = _read("dosage_gene_GRCh38.head.tsv")
    region_tsv = _read("dosage_region_GRCh38.head.tsv")
    # Build a tiny GRCh37 file with a different coordinate for AARS1.
    grch37 = (
        "#comment\n#Gene Symbol\tGene ID\tcytoBand\tGenomic Location\n"
        "AARS1\t16\t16q22.1\tchr16:OLD-COORD\n"
    )
    rows = parse.parse_dosage(gene_tsv, region_tsv, gene_tsv_grch37=grch37)
    aars1 = _by_symbol(rows, "AARS1")
    assert aars1["grch37"] == "chr16:OLD-COORD"
    # A gene not in the GRCh37 file keeps grch37 None.
    assert _by_symbol(rows, "A4GALT")["grch37"] is None


# ---------------------------------------------------------------------------
# Actionability
# ---------------------------------------------------------------------------


def test_parse_actionability_indexes_by_doc() -> None:
    brief = _load_json("actionability_brief_small.json")
    out = parse.parse_actionability(brief)
    by_id = {r["doc_id"]: r for r in out}
    ac1034 = by_id["AC1034"]
    assert ac1034["disease"] == "SCN1A-related seizure disorders"
    assert ac1034["curation_type"] == "Gene-Condition"
    assert ac1034["last_author"] == "Gilmore Mari"
    assert ac1034["pediatric_status"] == "Released"
    assert ac1034["pediatric_release"] == "1.0.1"
    assert ac1034["pediatric_sepio_iri"].endswith("/sepio/doc/AC1034")
    assert ac1034["genes"] == ["SCN1A"]
    assert ac1034["modes_of_inheritance"] == ["Autosomal Dominant"]


def test_parse_actionability_release_falls_back_to_date() -> None:
    brief = _load_json("actionability_brief_small.json")
    out = {r["doc_id"]: r for r in parse.parse_actionability(brief)}
    # AC1034 Adult has a release with only a date (no number).
    assert out["AC1034"]["adult_release"] == "Wed, 20 May 2026 00:00:00 -0000"


def test_parse_actionability_multi_gene_union() -> None:
    brief = _load_json("actionability_brief_small.json")
    out = {r["doc_id"]: r for r in parse.parse_actionability(brief)}
    genes = out["AC138"]["genes"]
    assert "BAG3" in genes and "TTN" in genes
    assert len(genes) == len(set(genes))


# ---------------------------------------------------------------------------
# ERepo
# ---------------------------------------------------------------------------


@pytest.fixture
def erepo_rows() -> list[dict[str, Any]]:
    return parse.parse_erepo(_read("erepo_bulk.head.tsv"))


def test_parse_erepo_count(erepo_rows: list[dict[str, Any]]) -> None:
    assert len(erepo_rows) == 5


def test_parse_erepo_splits_lists(erepo_rows: list[dict[str, Any]]) -> None:
    first = erepo_rows[0]
    assert first["caid"] == "CA281951"
    assert first["gene"] == "BRAF"
    assert first["assertion"] == "Likely Pathogenic"
    assert first["evidence_codes_met"] == ["PM6", "PM2", "PS4_Supporting", "PM1", "PP2", "PP3"]
    assert "PS1" in first["evidence_codes_not_met"]
    assert len(first["hgvs"]) == 27
    assert first["retracted"] is False
    assert first["uuid"] == "7808e324-29b8-43db-85ad-2f63caa01996"
    assert first["pubmed"] == []


def test_parse_erepo_pubmed_split(erepo_rows: list[dict[str, Any]]) -> None:
    gjb2 = next(r for r in erepo_rows if r["gene"] == "GJB2")
    assert "31160754" in gjb2["pubmed"]
    assert gjb2["clinvar_variation_id"] == "17000"


# ---------------------------------------------------------------------------
# Gene index
# ---------------------------------------------------------------------------


def test_build_gene_index_flags_and_aliases() -> None:
    validity = parse.parse_validity(_load_json("validity_api_small.json")["rows"])
    dosage = parse.parse_dosage(
        _read("dosage_gene_GRCh38.head.tsv"),
        _read("dosage_region_GRCh38.head.tsv"),
    )
    actionability = parse.parse_actionability(_load_json("actionability_brief_small.json"))
    summary = _load_json("erepo_summary_sample.json")
    genes, aliases = parse.build_gene_index(validity, dosage, actionability, summary)

    by_symbol = {g["symbol"]: g for g in genes}
    # AARS1 appears in validity and dosage.
    aars1 = by_symbol["AARS1"]
    assert aars1["has_validity"] == 1
    assert aars1["has_dosage"] == 1
    assert aars1["hgnc_id"] == "HGNC:20"
    # ABCA4 has an erepo variant count from the summary feed.
    assert by_symbol["ABCA4"]["erepo_variant_count"] == 139
    # HGNC alias maps back to the canonical symbol.
    alias_pairs = {(a["alias"], a["symbol"]) for a in aliases}
    assert ("HGNC:20", "AARS1") in alias_pairs
    assert ("hgnc:20", "AARS1") in alias_pairs


def test_to_json_is_compact_and_deterministic() -> None:
    assert parse.to_json(["b", "a"]) == '["b","a"]'
    assert parse.to_json([]) == "[]"
