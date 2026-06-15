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
_CANONICAL_VERBS = frozenset({"get", "search", "list", "resolve", "find", "compare", "compute"})
_NAMESPACE = "clingen"


async def test_tool_names_conform_to_standard_v1(mcp: FastMCP) -> None:
    names = sorted(t.name for t in await mcp.list_tools())
    assert names, "no tools registered on the facade"
    for name in names:
        assert _NAME_RE.match(name), f"{name!r} must match ^[a-z0-9_]{{1,50}}$"
        assert name.split("_", 1)[0] in _CANONICAL_VERBS, (
            f"{name!r} must start with a canonical verb {sorted(_CANONICAL_VERBS)}"
        )
        assert not name.startswith(f"{_NAMESPACE}_"), (
            f"{name!r} must not self-prefix the '{_NAMESPACE}' namespace "
            "token -- the gateway adds it"
        )
        assert _NAMESPACE not in name.split("_"), (
            f"{name!r} must not embed the source/self token '{_NAMESPACE}' as a "
            "name segment -- the gateway adds the namespace, so it would "
            f"double-prefix (e.g. '{_NAMESPACE}_{name}')"
        )
