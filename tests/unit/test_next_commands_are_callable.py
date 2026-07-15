"""Every `next_commands` affordance must be a call the target tool actually accepts.

`_meta.next_commands` is the fleet's chaining contract: a model executes the first entry to
advance without guessing. If it names a removed parameter, following it is a guaranteed
`invalid_input` — the exact regression that slipped through the required-selector migration
(issue #46): search_cspec emitted `get_cspec_criterion(criteria_id=...)` and the ERepo
cross-link emitted `get_cspec(affiliation=, gene=)`, both gone.

This guard validates every emitted command against the LIVE tool schema — the target tool
exists and every argument key is one of its declared parameters — so an affordance can never
again advertise a call the tool would reject.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from clingen_link.mcp import next_commands as nc
from clingen_link.mcp.tools.cspec import _search_next_commands
from clingen_link.mcp.tools.erepo import cspec_next_command

pytestmark = pytest.mark.asyncio


async def _tool_params(mcp: FastMCP) -> dict[str, set[str]]:
    """{tool_name -> its declared parameter names} from the live schema."""
    tools = await mcp.list_tools()
    return {tool.name: set((tool.parameters or {}).get("properties", {})) for tool in tools}


def _assert_callable(command: dict[str, object], params: dict[str, set[str]]) -> None:
    tool = command["tool"]
    assert tool in params, f"next_command targets unknown tool {tool!r}"
    unknown = set(command.get("arguments") or {}) - params[str(tool)]
    assert not unknown, f"{tool} next_command passes non-parameters {unknown}"


async def test_cspec_search_criterion_hit_chains_to_the_natural_key(tool_mcp: FastMCP) -> None:
    params = await _tool_params(tool_mcp)
    # A criterion hit now carries (gn_id, code, rule_set_id) — the get_cspec_criterion key.
    hit = {
        "entity_type": "criterion",
        "gn_id": "GN092",
        "criteria_id": "55",
        "code": "PVS1",
        "rule_set_id": "9",
    }
    [command] = _search_next_commands([hit])

    assert command == {
        "tool": "get_cspec_criterion",
        "arguments": {"gn_id": "GN092", "code": "PVS1", "rule_set_id": "9"},
    }
    _assert_callable(command, params)


async def test_cspec_search_criterion_without_code_falls_back_to_get_cspec(
    tool_mcp: FastMCP,
) -> None:
    params = await _tool_params(tool_mcp)
    # No resolvable code (e.g. a spec-level criteria_id) → get_cspec(gn_id), never a criteria_id.
    [command] = _search_next_commands([{"entity_type": "spec", "gn_id": "GN092"}])

    assert command == {"tool": "get_cspec", "arguments": {"gn_id": "GN092"}}
    _assert_callable(command, params)


async def test_the_shared_next_command_builders_are_callable(tool_mcp: FastMCP) -> None:
    """for_gene / for_disease / for_variant must emit calls the target tools accept.

    for_variant emitted the removed `caid=` selector (issue #46); this guards the whole family.
    """
    params = await _tool_params(tool_mcp)
    for builder, arg in (
        (nc.for_gene, "BRCA1"),
        (nc.for_disease, "MONDO:0007254"),
        (nc.for_variant, "CA123456"),
    ):
        for command in builder(arg):
            _assert_callable(command, params)


async def test_erepo_ambiguous_crosslink_chains_to_list_cspecs(tool_mcp: FastMCP) -> None:
    params = await _tool_params(tool_mcp)
    command = cspec_next_command(
        "https://cspec.clinicalgenome.org/cspec/ui/svi/affiliation/50087",
        gene="BRCA1",
        resolve=lambda aff, gene: ["GN092", "GN101"],
    )
    assert command is not None
    _assert_callable(command, params)


async def test_live_tool_next_commands_are_callable(tool_mcp: FastMCP) -> None:
    """Drive the tools that have fixture data and validate every affordance they emit."""
    params = await _tool_params(tool_mcp)
    seeds = [
        ("search_genes", {"query": "AAGAB"}),
        ("get_gene_dosage", {"gene_symbol": "AAGAB"}),
        ("get_gene_summary", {"gene_symbol": "AAGAB"}),
        ("search_validity", {"size": 5}),
        ("search_dosage", {"size": 5}),
        ("get_variant_interpretation", {"variant_id": "CA281951"}),
    ]
    for name, args in seeds:
        result = await tool_mcp.call_tool(name, args)
        envelope = result.structured_content or {}
        for command in (envelope.get("_meta") or {}).get("next_commands", []):
            _assert_callable(command, params)
