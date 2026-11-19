"""D1 (Tool-Schema Documentation v1.1): every tool description leads with what it returns."""

from __future__ import annotations

import re

import pytest
from fastmcp import FastMCP

# ponytail: copied from genefoundry-router scripts/surface.py (lead_states_return); re-vendor
# byte-identically if this repo ever vendors surface.py.
LEAD_CHARS = 300
_LEAD_VERBS = [
    "get",
    "fetch",
    "retrieve",
    "list",
    "look",
    "lookup",
    "search",
    "find",
    "resolve",
    "report",
    "read",
    "predict",
    "annotate",
    "compute",
    "calculate",
    "count",
    "summarize",
    "summarise",
    "map",
    "translate",
    "convert",
    "describe",
    "show",
    "check",
    "validate",
    "classify",
    "score",
    "rank",
    "query",
    "browse",
    "discover",
    "explain",
    "compare",
    "diff",
    "normalize",
    "normalise",
    "expand",
    "evaluate",
    "assess",
    "enumerate",
    "identify",
    "locate",
    "export",
    "download",
    "match",
    "suggest",
    "recommend",
    "inspect",
    "generate",
    "render",
    "estimate",
]
_VERB_LEAD = re.compile(
    rf"^(?:use this (?:tool )?to )?(?:{'|'.join(_LEAD_VERBS)})(?:s|es|ies)?\b", re.IGNORECASE
)
_RETURNS = re.compile(
    r"(?<!not )(?<!n't )(?<!n\u2019t )(?<!never )\breturn(?:s|ed|ing)?\b(?!\s*[~<\d])|→|->",
    re.IGNORECASE,
)
_FIELD_LIST = re.compile(r"\w+\W+\w+[^:]*:\s+[^.;:]+,")
_SENTENCE_END = re.compile(r"(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+")


def lead_states_return(description: str | None) -> bool:
    text = " ".join((description or "").split())
    lead = _SENTENCE_END.split(text, maxsplit=1)[0][:LEAD_CHARS]
    return bool(_VERB_LEAD.match(lead) or _RETURNS.search(lead) or _FIELD_LIST.search(lead))


@pytest.mark.asyncio
async def test_every_tool_description_leads_with_its_return(tool_mcp: FastMCP) -> None:
    failing = [t.name for t in await tool_mcp.list_tools() if not lead_states_return(t.description)]
    assert failing == []


def test_rule_rejects_usage_only_lead() -> None:
    assert not lead_states_return("Use this when a caller needs constraint metrics for a gene.")
    assert lead_states_return("Use this when a caller needs X; returns a, b and c.")
