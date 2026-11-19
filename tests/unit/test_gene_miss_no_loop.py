"""A gene miss must never route the caller into a resolve <-> lookup loop (issue #151).

The gene index holds only ClinGen-curated genes and every gene-keyed tool (search_genes
included) reads it, so a miss in one is a miss in all. Production showed callers bouncing
get_gene_validity -> search_genes -> get_gene_validity on real-but-uncurated genes. This
guard builds the miss graph (tool -> fallback_tool / next_commands targets) over every
gene-keyed tool and asserts no miss points at another gene-keyed tool, so no 2-cycle exists.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

pytestmark = pytest.mark.asyncio

UNCURATED = "ZZZNOPE"
GENE_KEYED: dict[str, dict[str, object]] = {
    "search_genes": {"query": UNCURATED},
    "get_gene_summary": {"gene_symbol": UNCURATED},
    "get_gene_validity": {"gene_symbol": UNCURATED},
    "get_gene_dosage": {"gene_symbol": UNCURATED},
    "get_gene_actionability": {"gene_symbol": UNCURATED},
    "search_validity": {"gene_symbol": UNCURATED},
    "search_actionability": {"gene_symbol": UNCURATED},
    "get_variant_interpretations": {"gene_symbol": UNCURATED},
    "list_cspecs": {"gene_symbol": UNCURATED},
}


async def test_gene_miss_graph_has_no_cycle(tool_mcp: FastMCP) -> None:
    edges: dict[str, set[str]] = {}
    for tool, args in GENE_KEYED.items():
        payload = (await tool_mcp.call_tool(tool, args)).structured_content or {}
        assert payload["error_code"] == "not_found", tool
        targets = {c["tool"] for c in payload["_meta"]["next_commands"]}
        targets.add(payload["fallback_tool"])
        edges[tool] = targets
        # The prose must not send the caller back to the same index either.
        assert "Resolve with search_genes" not in payload["message"], tool
        assert "Check the symbol" not in payload["message"], tool

    for src, targets in edges.items():
        back = targets & GENE_KEYED.keys()
        assert not back, f"{src} miss routes to gene-keyed {back}: loop risk"
        for dst in targets:
            assert src not in edges.get(dst, set()), f"2-cycle {src} <-> {dst}"
