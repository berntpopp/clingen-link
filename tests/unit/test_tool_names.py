"""Tool-name compliance with the GeneFoundry Tool-Naming Standard v1.

Every registered tool must be unprefixed, snake_case, <= 50 chars, and start with
a canonical verb so it composes cleanly behind the ``genefoundry-router`` gateway,
which mounts this server under the ``clingen`` namespace (tools surface as
``clingen_<tool>``). The embedded ``clingen`` source token is forbidden anywhere in
a leaf name -- the gateway adds the namespace, so a self/source token would
double-prefix (e.g. ``clingen_get_clingen_diagnostics``). Guards against future
drift. See issue berntpopp/clingen-link#4.
"""

from __future__ import annotations

import re

from fastmcp import FastMCP

_NAME_RE = re.compile(r"^[a-z0-9_]{1,50}$")
# Tier-1 read/query canon — Tool-Naming Standard v1.1, ratified 2026-06-30.
_CANONICAL_VERBS = frozenset(
    {"get", "search", "list", "resolve", "find", "compare", "compute", "map"}
)
# Tier-2 sanctioned action/compute verbs — v1.1.
_TIER2_VERBS = frozenset(
    {
        "predict",
        "annotate",
        "recode",
        "liftover",
        "analyze",
        "score",
        "submit",
        "export",
        "generate",
        "download",
    }
)
_ALLOWED_VERBS = _CANONICAL_VERBS | _TIER2_VERBS
_NAMESPACE = "clingen"


async def test_tool_names_conform_to_standard_v1(mcp: FastMCP) -> None:
    tools = await mcp.list_tools()
    assert tools, "no tools registered on the facade"
    for tool in tools:
        name = tool.name
        tags = set(tool.tags or ())
        assert _NAME_RE.match(name), f"{name!r} must match ^[a-z0-9_]{{1,50}}$"
        assert not name.startswith(f"{_NAMESPACE}_"), (
            f"{name!r} must not self-prefix the '{_NAMESPACE}' namespace "
            "token -- the gateway adds it"
        )
        assert _NAMESPACE not in name.split("_"), (
            f"{name!r} must not embed the source/self token '{_NAMESPACE}' as a "
            "name segment -- the gateway adds the namespace, so it would "
            f"double-prefix (e.g. '{_NAMESPACE}_{name}')"
        )
        # ops/meta tools (health checks, warmup, diagnostics, etc.) are exempt
        # from the verb rule — fleet ops/meta carve-out (v1.1).
        if "ops" in tags or "meta" in tags:
            continue
        assert name.split("_", 1)[0] in _ALLOWED_VERBS, (
            f"{name!r} must start with a canonical verb {sorted(_ALLOWED_VERBS)}"
        )
