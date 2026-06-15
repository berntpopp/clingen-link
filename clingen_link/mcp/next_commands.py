"""Shared builders for _meta.next_commands entries.

Every tool emits next_commands in one shape: a list of {tool, arguments}
dicts whose arguments are directly callable (never empty). Centralising the
builders keeps the contract identical across tools.
"""

from __future__ import annotations

from typing import Any


def cmd(tool: str, **arguments: Any) -> dict[str, Any]:
    """One next_commands entry. Arguments must be directly callable (never empty)."""
    return {"tool": tool, "arguments": arguments}


def for_gene(symbol: str) -> list[dict[str, Any]]:
    """Standard follow-ups for a resolved gene: cross-domain summary first."""
    return [
        cmd("get_gene_summary", gene_symbol=symbol),
        cmd("get_gene_validity", gene_symbol=symbol),
    ]


def for_disease(mondo: str) -> list[dict[str, Any]]:
    """Standard follow-ups for a disease: validity then variant interpretations."""
    return [
        cmd("search_validity", mondo=mondo),
        cmd("get_variant_interpretations", disease=mondo),
    ]


def for_variant(caid: str) -> list[dict[str, Any]]:
    """Standard follow-up for a resolved variant: full ERepo interpretation."""
    return [
        cmd("get_variant_interpretation", caid=caid),
    ]
