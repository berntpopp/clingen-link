"""An unrecognised filter value is REJECTED, never silently matched to nothing.

Response-Envelope v1.1: "silent omission is not compliant." A filter that accepts a value
it cannot match and answers `success: true` with zero rows is indistinguishable from "the
data genuinely has none" — the caller cannot tell a typo from an empty result, and an agent
happily reports "there are no such genes" (issue #46, and the fleet-wide bug the behaviour
gate exists to catch).

Two mechanisms, one contract:

* a CLOSED vocabulary is declared as an ``enum`` (dosage codes, curation statuses) and the
  schema rejects an out-of-enum value before the tool body runs;
* an IDENTIFIER (a gene, an ISCA region, a MONDO id, an expert panel, a disease name) is
  validated against the snapshot's own index and rejected with ``not_found`` naming the
  parameter and the tool that resolves it.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

SENTINEL = "__gf_conformance_no_such_value__"


async def _call(mcp: FastMCP, tool: str, args: dict[str, object]) -> tuple[dict, bool]:
    result = await mcp.call_tool(tool, args)
    envelope = dict(result.structured_content or {})
    is_error = bool(getattr(result, "is_error", False))
    return envelope, is_error


def _rows(envelope: dict) -> list:
    for key, value in envelope.items():
        if not key.startswith("_") and isinstance(value, list):
            return value
    return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "param"),
    [
        ("search_dosage", "region"),
        ("search_dosage", "cytoband"),
        ("search_validity", "gene_symbol"),
        ("search_validity", "disease"),
        ("search_validity", "mondo"),
        ("search_validity", "expert_panel"),
        ("search_actionability", "gene_symbol"),
        ("search_actionability", "disease"),
        ("get_variant_interpretations", "gene_symbol"),
        ("get_variant_interpretations", "disease"),
        ("get_variant_interpretations", "expert_panel"),
        ("list_cspecs", "gene_symbol"),
        ("list_cspecs", "affiliation"),
    ],
)
class TestUnrecognisedFilterValues:
    async def test_is_an_error_not_an_empty_result(
        self, tool_mcp: FastMCP, tool: str, param: str
    ) -> None:
        envelope, is_error = await _call(tool_mcp, tool, {param: SENTINEL})

        assert envelope.get("success") is False, (
            f"{tool}.{param}={SENTINEL!r} returned success with "
            f"{len(_rows(envelope))} rows — the silently-empty filter"
        )
        assert is_error is True
        assert envelope["error_code"] in {"invalid_input", "not_found"}

    async def test_names_the_parameter_so_the_model_can_self_correct(
        self, tool_mcp: FastMCP, tool: str, param: str
    ) -> None:
        envelope, _ = await _call(tool_mcp, tool, {param: SENTINEL})

        text = f"{envelope.get('message')} {envelope.get('recovery')}"
        assert param in text, f"{tool}: {text!r} names no parameter"


@pytest.mark.asyncio
class TestClosedVocabulariesAreDeclaredEnums:
    @pytest.mark.parametrize(
        ("tool", "param"),
        [
            ("search_dosage", "haplo_score"),
            ("search_dosage", "triplo_score"),
            ("search_dosage", "record_type"),
            ("search_validity", "classification"),
            ("search_validity", "moi"),
            ("search_actionability", "assertion"),
            ("list_cspecs", "status"),
        ],
    )
    async def test_an_out_of_enum_value_is_invalid_input(
        self, tool_mcp: FastMCP, tool: str, param: str
    ) -> None:
        envelope, is_error = await _call(tool_mcp, tool, {param: SENTINEL})

        assert envelope.get("success") is False
        assert is_error is True
        assert envelope["error_code"] == "invalid_input"

    @pytest.mark.parametrize(
        ("tool", "param", "enum"),
        [
            ("search_validity", "moi", {"AD", "AR", "XL", "MT", "SD", "UD"}),
        ],
    )
    async def test_every_advertised_value_is_one_the_data_can_match(
        self, tool_mcp: FastMCP, tool: str, param: str, enum: set[str]
    ) -> None:
        """The contract-truth rule: the schema must not advertise a value the runtime never
        matches. `moi` advertised "Undetermined"; the snapshot stores "UD", so the documented
        value was dead on arrival — a silently-empty filter with a declared enum.
        """
        declared_tool = await tool_mcp.get_tool(tool)
        schema = declared_tool.parameters["properties"][param]
        declared = schema.get("enum") or next(
            (b["enum"] for b in schema.get("anyOf", []) if "enum" in b), []
        )

        assert set(declared) == enum


@pytest.mark.asyncio
class TestLegitimateEmptyResultsStillSucceed:
    async def test_a_known_value_with_no_matches_is_success_zero(self, tool_mcp: FastMCP) -> None:
        """The point is to distinguish a bad VALUE from an empty INTERSECTION, not to error
        whenever a result set is empty. Both filters exist; together they match nothing."""
        envelope, is_error = await _call(
            tool_mcp,
            "search_validity",
            {"gene_symbol": "AAGAB", "classification": "Refuted"},
        )

        assert envelope["success"] is True
        assert is_error is False
        assert envelope["total"] == 0


@pytest.mark.asyncio
class TestFreeTextSearchIsNotAFilter:
    async def test_a_nonsense_query_is_a_legitimate_empty_result(self, tool_mcp: FastMCP) -> None:
        """A search box may legitimately find nothing; only VALUE filters are validated."""
        envelope, is_error = await _call(tool_mcp, "search_dosage", {"query": SENTINEL})

        assert envelope["success"] is True
        assert is_error is False
        assert envelope["total"] == 0


@pytest.mark.asyncio
class TestReturnedValuesRoundTrip:
    async def test_an_expert_panel_from_a_record_is_a_valid_filter(self, tool_mcp: FastMCP) -> None:
        listed, _ = await _call(tool_mcp, "search_validity", {"size": 1})
        panel = listed["records"][0]["expert_panel"]

        envelope, _ = await _call(tool_mcp, "search_validity", {"expert_panel": panel})

        assert envelope["success"] is True
        assert envelope["total"] > 0
