"""The README's ``## Tools`` table must match the registered tool surface exactly.

GeneFoundry README Standard v1 makes the tools table the one section allowed to grow
with the server -- and therefore the one section that silently rots. This guard closes
that gap: add, rename, or drop a tool without touching the README and CI fails.

The live tool list is taken the same way ``test_tool_names.py`` takes it -- the ``mcp``
fixture (``create_clingen_mcp()``) plus ``await mcp.list_tools()`` -- so the two guards
can never disagree about what "registered" means.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastmcp import FastMCP

_README = Path(__file__).resolve().parents[2] / "README.md"

# A table row's first cell, when it is a single backticked tool name: | `get_gene_summary` | ... |
_TOOL_ROW = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")


def _readme_tool_table() -> list[str]:
    """Return the tool names listed in the README's ``## Tools`` table, in order."""
    lines = _README.read_text(encoding="utf-8").splitlines()

    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "## Tools")
    except StopIteration:  # pragma: no cover - the README linter already forbids this
        raise AssertionError("README.md has no '## Tools' section") from None

    names: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):  # next section: the table cannot span headings
            break
        match = _TOOL_ROW.match(line)
        if match:
            names.append(match.group(1))
    return names


async def test_readme_tools_table_matches_registered_tools(mcp: FastMCP) -> None:
    registered = {tool.name for tool in await mcp.list_tools()}
    assert registered, "no tools registered on the facade"

    documented = _readme_tool_table()
    assert documented, "README '## Tools' table lists no tools"

    missing = sorted(registered - set(documented))
    extra = sorted(set(documented) - registered)
    assert not missing, f"registered but absent from the README '## Tools' table: {missing}"
    assert not extra, f"listed in the README '## Tools' table but not registered: {extra}"


async def test_readme_tool_table_has_no_duplicate_rows(mcp: FastMCP) -> None:
    documented = _readme_tool_table()
    assert len(documented) == len(set(documented)), (
        f"duplicate rows in the README '## Tools' table: {sorted(documented)}"
    )
