"""Response-Envelope Standard v1.2: every data tool's result validates against the
published JSON Schema. The two CSpec detail tools need a spec the small snapshot does not
hold; their envelopes are validated in tests/unit/mcp/test_cspec_tools.py."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import jsonschema
from fastmcp import FastMCP

from tests.conformance.envelope_schema import SCHEMA, SCHEMA_FILE, assert_data_envelope

# Orient a client and return no upstream data: outside the v1.2 provenance rule.
OPS_TOOLS = {"get_server_capabilities", "get_diagnostics"}
VALIDATED_ELSEWHERE = {"get_cspec", "get_cspec_criterion"}
# The schema example of this tool is not in the small snapshot.
ARGS = {"get_variant_interpretation": {"variant_id": "CA281951"}}


def test_vendored_schema_matches_pinned_sha256() -> None:
    pinned = SCHEMA_FILE.with_suffix(".sha256").read_text().split()[0]
    assert hashlib.sha256(SCHEMA_FILE.read_bytes()).hexdigest() == pinned


async def test_every_data_tool_result_validates_and_carries_provenance(tool_mcp: FastMCP) -> None:
    checked = 0
    for tool in await tool_mcp.list_tools():
        if tool.name in OPS_TOOLS | VALIDATED_ELSEWHERE:
            continue
        props = tool.parameters.get("properties", {})
        args: dict[str, Any] = ARGS.get(tool.name) or {
            k: props[k]["examples"][0] for k in tool.parameters.get("required", [])
        }
        result = await tool_mcp.call_tool(tool.name, args)
        envelope = result.structured_content
        assert envelope is not None, tool.name
        assert json.loads(result.content[0].text) == envelope, tool.name
        try:
            assert_data_envelope(envelope)
        except (AssertionError, jsonschema.ValidationError) as exc:
            raise AssertionError(f"{tool.name}: {exc}") from exc
        assert envelope["_meta"]["source"] == "clingen"
        checked += 1
    assert checked == 13


async def test_error_envelope_validates_against_schema(tool_mcp: FastMCP) -> None:
    result = await tool_mcp.call_tool("get_gene_validity", {"gene_symbol": "NOTAGENE1"})
    assert result.structured_content["success"] is False
    jsonschema.validate(result.structured_content, SCHEMA)
