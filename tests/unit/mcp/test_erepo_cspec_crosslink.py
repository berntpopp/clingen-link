"""Tests for the ERepo->CSpec next_commands cross-link (Task 11)."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from clingen_link.mcp.tools.erepo import cspec_next_command


def test_unique_affiliation_gene_emits_gn_id() -> None:
    cmd_ = cspec_next_command(
        "https://cspec.clinicalgenome.org/cspec/ui/svi/affiliation/50087",
        gene="BRCA1",
        resolve=lambda aff, gene: ["GN092"],
    )
    assert cmd_ == {"tool": "get_cspec", "arguments": {"gn_id": "GN092"}}


def test_ambiguous_emits_affiliation_plus_gene() -> None:
    cmd_ = cspec_next_command(
        "https://cspec.clinicalgenome.org/cspec/ui/svi/affiliation/50087",
        gene="BRCA1",
        resolve=lambda aff, gene: ["GN092", "GN101"],
    )
    assert cmd_ == {
        "tool": "get_cspec",
        "arguments": {"affiliation": "50087", "gene": "BRCA1"},
    }


def test_none_when_no_affiliation() -> None:
    assert cspec_next_command(None, gene="BRCA1", resolve=lambda a, g: []) is None


async def _call(mcp: FastMCP, name: str, args: dict[str, object]) -> dict[str, object]:
    result = await mcp.call_tool(name, args)
    return result.structured_content or {}


@pytest.mark.asyncio
class TestVariantDetailCarriesCspecCommand:
    async def test_get_variant_interpretation_emits_get_cspec(self, tool_mcp: FastMCP) -> None:
        # CA281951 (BRAF) carries guideline .../affiliation/50021 in the erepo fixture.
        # The test snapshot has no cspec rows, so the affiliation resolves to no unique
        # gn_id -> the response must still carry the {affiliation, gene} get_cspec affordance.
        payload = await _call(tool_mcp, "get_variant_interpretation", {"variant_id": "CA281951"})
        assert payload["success"] is True
        cmds = payload["_meta"]["next_commands"]
        get_cspec = [c for c in cmds if c["tool"] == "get_cspec"]
        assert len(get_cspec) == 1
        assert get_cspec[0]["arguments"] == {"affiliation": "50021", "gene": "BRAF"}
