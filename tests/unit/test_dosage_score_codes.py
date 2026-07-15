"""The ClinGen dosage score column is a closed code vocabulary, not prose.

Two defects, one root cause (issue #46):

* **D1** — the documented codes ``30`` / ``40`` silently returned 0 rows. The ETL
  *decoded* them into their description text before storing, so the snapshot's
  ``haplo_score`` column never held the codes the schema advertises.
* **D2** — ``get_gene_dosage(CFTR).haplo_score`` therefore returned the sentence
  "Gene associated with autosomal recessive phenotype" in a numeric field.

Upstream (``ClinGen_gene_curation_list_GRCh38.tsv``) publishes a Score column whose
vocabulary is ``{0,1,2,3,30,40}`` (plus the ``Not yet evaluated`` sentinel and empty
in the triplosensitivity column) and a *separate* Description column carrying the
prose. The snapshot must keep them separate too.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from clingen_link.etl.parse import parse_dosage
from clingen_link.exceptions import SnapshotBuildError
from clingen_link.store import queries
from clingen_link.vocab import DOSAGE_SCORE_CODES

# One upstream row per shape, in the real column order (23 columns).
_HEADER = "#Gene Symbol\tGene ID\tcytoBand\tGenomic Location\tHaploinsufficiency Score\n"


def _gene_row(symbol: str, haplo: str, haplo_desc: str, triplo: str, triplo_desc: str) -> str:
    cells = [""] * 23
    cells[0], cells[1], cells[2], cells[3] = symbol, "1080", "7q31.2", "chr7:1-2"
    cells[4], cells[5] = haplo, haplo_desc
    cells[12], cells[13] = triplo, triplo_desc
    return "\t".join(cells)


class TestEtlKeepsTheUpstreamCode:
    def test_code_30_is_stored_as_the_code_not_the_description(self) -> None:
        """The Score column is the canonical code; the Description column is the prose."""
        tsv = _HEADER + _gene_row(
            "CFTR", "30", "Gene associated with autosomal recessive phenotype", "0", "No evidence"
        )
        record = parse_dosage(tsv, "")[0]

        assert record["haplo_score"] == "30"
        assert record["haplo_description"] == "Gene associated with autosomal recessive phenotype"

    def test_code_40_is_stored_as_the_code(self) -> None:
        tsv = _HEADER + _gene_row("ABC", "40", "Dosage sensitivity unlikely", "40", "Unlikely")
        record = parse_dosage(tsv, "")[0]

        assert record["haplo_score"] == "40"
        assert record["triplo_score"] == "40"

    def test_not_yet_evaluated_is_not_a_score(self) -> None:
        """Upstream ships this sentence *in the score column*; it is an absent score, not a code."""
        tsv = _HEADER + _gene_row(
            "ABAT", "30", "AR phenotype", "Not yet evaluated", "Not yet evaluated"
        )
        record = parse_dosage(tsv, "")[0]

        assert record["triplo_score"] is None
        assert record["triplo_description"] == "Not yet evaluated"

    def test_empty_score_is_null(self) -> None:
        tsv = _HEADER + _gene_row("ABC", "", "", "", "")
        record = parse_dosage(tsv, "")[0]

        assert record["haplo_score"] is None

    def test_an_unknown_code_fails_the_build_loudly(self) -> None:
        """Vocabulary drift must break the ETL, never ship a value no filter can reach."""
        tsv = _HEADER + _gene_row("ABC", "99", "brand new code", "0", "No evidence")

        with pytest.raises(SnapshotBuildError, match="dosage score"):
            parse_dosage(tsv, "")


class TestSnapshotVocabulary:
    def test_every_stored_score_is_a_code_or_null(self, store) -> None:  # type: ignore[no-untyped-def]
        """The invariant behind D2: a score field never holds prose."""
        with store.connection() as conn:
            rows = conn.execute("SELECT haplo_score, triplo_score FROM dosage").fetchall()

        stored = {value for row in rows for value in (row[0], row[1]) if value is not None}
        assert stored, "fixture snapshot has no dosage rows"
        assert stored <= DOSAGE_SCORE_CODES, f"non-code values in a score column: {stored}"


@pytest.mark.asyncio
class TestSearchDosageFindsTheDocumentedCodes:
    async def test_code_30_returns_the_genes_that_carry_it(self, tool_mcp: FastMCP) -> None:
        """D1: the documented code 30 must return its rows, not silently zero."""
        result = await tool_mcp.call_tool("search_dosage", {"haplo_score": "30", "size": 100})
        payload = result.structured_content or {}

        assert payload["success"] is True
        assert payload["total"] > 0, "code 30 returned 0 rows — the silently-empty filter"
        assert all(r["haplo_score"] == "30" for r in payload["records"])

    async def test_an_unrecognised_code_is_rejected_not_silently_empty(
        self, tool_mcp: FastMCP
    ) -> None:
        """Response-Envelope v1.1: silent omission is not compliant."""
        result = await tool_mcp.call_tool("search_dosage", {"haplo_score": "99"})
        payload = result.structured_content or {}

        assert payload["success"] is False
        assert payload["error_code"] == "invalid_input"
        assert result.is_error is True
        # The model must be able to self-correct: the message names the parameter.
        assert "haplo_score" in str(payload.get("message")) + str(payload.get("recovery"))


@pytest.mark.asyncio
class TestGetGeneDosageScoreIsNumeric:
    async def test_haplo_score_is_the_code_and_the_prose_has_its_own_field(
        self, tool_mcp: FastMCP
    ) -> None:
        """D2: a numeric field must never return a sentence."""
        result = await tool_mcp.call_tool(
            "get_gene_dosage", {"gene_symbol": "A4GALT", "response_mode": "full"}
        )
        record = (result.structured_content or {})["records"][0]

        assert record["haplo_score"] == "30"
        assert record["haplo_interpretation"] == (
            "Gene associated with autosomal recessive phenotype"
        )

    async def test_no_score_field_ever_holds_a_non_code(self, tool_mcp: FastMCP) -> None:
        result = await tool_mcp.call_tool("search_dosage", {"size": 100, "response_mode": "full"})
        records = (result.structured_content or {})["records"]

        for record in records:
            for field in ("haplo_score", "triplo_score"):
                value = record.get(field)
                assert value is None or value in DOSAGE_SCORE_CODES, (
                    f"{record.get('symbol') or record.get('isca_id')}.{field} = {value!r}"
                )


@pytest.mark.asyncio
class TestScoreRoundTrips:
    async def test_a_score_from_get_gene_dosage_is_a_valid_search_filter(
        self, tool_mcp: FastMCP
    ) -> None:
        """The audit's core complaint: the value returned could not be fed back in."""
        detail = await tool_mcp.call_tool("get_gene_dosage", {"gene_symbol": "A4GALT"})
        score = (detail.structured_content or {})["records"][0]["haplo_score"]

        result = await tool_mcp.call_tool("search_dosage", {"haplo_score": score, "size": 100})
        payload = result.structured_content or {}

        assert payload["success"] is True
        assert payload["total"] > 0

    async def test_the_queried_column_is_indexed_on_codes(self, store) -> None:  # type: ignore[no-untyped-def]
        with store.connection() as conn:
            rows, total = queries.search_dosage(conn, haplo_score="30", size=100)

        assert total > 0
        assert all(r["haplo_score"] == "30" for r in rows)
