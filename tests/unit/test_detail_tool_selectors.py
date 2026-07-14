"""A detail tool declares the identifier it needs; it does not accept a call it cannot answer.

get_cspec, get_cspec_criterion and get_variant_interpretation each took several OPTIONAL
selectors and rejected the empty call at runtime ("Supply one of gn_id, affiliation, or
gene."). Three consequences, all bad:

* the schema said every argument was optional, so a call with none is *schema-valid* — the
  tool advertised a call it always refuses;
* the refusal came back as `not_found` ("Identifier well-formed but absent in the ClinGen
  snapshot") for an identifier the caller never supplied, telling the model the thing does
  not exist rather than that it forgot an argument;
* get_cspec's message named `gene` — a parameter that does not exist (the real one is
  `gene_symbol`), so following the message literally produced a second, different error.

Each tool now REQUIRES the identifier it looks up, with an `examples` value, so the schema
alone is enough to construct a valid call (issue #46, audit defect 4).
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

pytestmark = pytest.mark.asyncio

# (tool, the identifier it now requires)
_DETAIL_TOOLS = [
    ("get_cspec", ["gn_id"]),
    ("get_cspec_criterion", ["gn_id", "code"]),
    ("get_variant_interpretation", ["variant_id"]),
    ("get_gene_dosage", ["gene_symbol"]),
    ("get_gene_validity", ["gene_symbol"]),
]


@pytest.mark.parametrize(("tool", "required"), _DETAIL_TOOLS)
class TestTheSchemaSaysWhatIsRequired:
    async def test_the_identifier_is_declared_required(
        self, tool_mcp: FastMCP, tool: str, required: list[str]
    ) -> None:
        declared = await tool_mcp.get_tool(tool)

        assert sorted(declared.parameters.get("required", [])) == sorted(required)

    async def test_every_required_parameter_carries_an_example(
        self, tool_mcp: FastMCP, tool: str, required: list[str]
    ) -> None:
        """TOOL-SCHEMA-DOCUMENTATION S2 — and what makes the tool probeable at all: the
        behaviour gate builds its valid call out of these examples, and reports a tool it
        cannot construct a call for as UNGATED, never as passing."""
        declared = await tool_mcp.get_tool(tool)

        for name in required:
            examples = declared.parameters["properties"][name].get("examples")
            assert examples, f"{tool}.{name} carries no examples"

    async def test_the_empty_call_is_invalid_input_not_not_found(
        self, tool_mcp: FastMCP, tool: str, required: list[str]
    ) -> None:
        result = await tool_mcp.call_tool(tool, {})
        envelope = result.structured_content or {}

        assert result.is_error is True
        # not_found would tell the model the TOOL/thing does not exist. It forgot an argument.
        assert envelope["error_code"] == "invalid_input"


class TestGetVariantInterpretationTakesOneVariantId:
    """The three id shapes are preserved — as one required parameter, not three optional ones."""

    async def test_a_caid(self, tool_mcp: FastMCP) -> None:
        result = await tool_mcp.call_tool("get_variant_interpretation", {"variant_id": "CA281951"})
        envelope = result.structured_content or {}

        assert envelope["success"] is True
        assert envelope["interpretation"]["caid"] == "CA281951"

    async def test_a_clinvar_variation_id(self, tool_mcp: FastMCP) -> None:
        result = await tool_mcp.call_tool("get_variant_interpretation", {"variant_id": "17000"})
        envelope = result.structured_content or {}

        assert envelope["success"] is True

    async def test_an_unrecognisable_id_shape_is_invalid_input_naming_the_shapes(
        self, tool_mcp: FastMCP
    ) -> None:
        result = await tool_mcp.call_tool(
            "get_variant_interpretation", {"variant_id": "not-an-identifier"}
        )
        envelope = result.structured_content or {}

        assert result.is_error is True
        assert envelope["error_code"] == "invalid_input"
        assert "variant_id" in envelope["message"]

    async def test_minimal_keeps_the_identifiers(self, tool_mcp: FastMCP) -> None:
        """minimal returned an EMPTY object for the interpretation — a silent-empty."""
        result = await tool_mcp.call_tool(
            "get_variant_interpretation", {"variant_id": "CA281951", "response_mode": "minimal"}
        )
        envelope = result.structured_content or {}

        assert envelope["success"] is True
        assert envelope["interpretation"]["caid"] == "CA281951"


class TestGetCspecCriterionTakesItsNaturalKey:
    """The success path is exercised against the cspec fixtures in test_cspec_tools.py."""

    async def test_a_well_formed_code_the_spec_does_not_define_is_not_found(
        self, tool_mcp: FastMCP
    ) -> None:
        result = await tool_mcp.call_tool("get_cspec_criterion", {"gn_id": "GN092", "code": "PS4"})
        envelope = result.structured_content or {}

        assert result.is_error is True
        assert envelope["error_code"] == "not_found"

    async def test_a_malformed_code_is_invalid_input(self, tool_mcp: FastMCP) -> None:
        result = await tool_mcp.call_tool("get_cspec_criterion", {"gn_id": "GN092", "code": "PZZ9"})
        envelope = result.structured_content or {}

        assert result.is_error is True
        assert envelope["error_code"] == "invalid_input"
