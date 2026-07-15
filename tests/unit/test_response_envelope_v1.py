"""Locks the ratified GeneFoundry Response-Envelope Standard v1 contract at
clingen-link's MCP wrapper boundary (``clingen_link.mcp.errors.run_mcp_tool``).

clingen-link is the fleet's REFERENCE implementation of the *flat banner* frame
(see ``docs/RESPONSE-ENVELOPE-STANDARD-v1.md`` in the router repo and the
2026-06-30 finalization brief, OQ4 -> Option A): the strict nested ``error:{}``
/ ``meta`` (renamed from ``_meta``) Rules body is non-normative "v2 future";
what is actually ratified as v1 is the shape this repo already ships:

  - SUCCESS: ``{"success": True, <payload>, "_meta": {..., "unsafe_for_clinical_use": True}}``
  - FAILURE: a FLAT in-band envelope ``{"success": False, "error_code", "message",
    "retryable", "recovery_action", ..., "_meta": {"tool": ..., "unsafe_for_clinical_use": True, ...}}``
    -- never a bare exception.

This test is the fleet-wide CI regression gate for that contract: it is meant to be
copy/adapted (near-)verbatim into the ~16 other already-conformant ``-link`` backends
so future drift at the wrapper boundary fails CI instead of shipping quietly.

Ground-truth note (verified against the installed code, not assumed): the wrapper
injects ``_meta.tool`` only on the ERROR path (``mcp_tool_error``); on the SUCCESS
path, ``_meta`` carries ``unsafe_for_clinical_use`` but NOT ``tool`` --
``_provenance_meta()`` never reads its ``context`` argument. Backends adapting this
test should assert accordingly rather than assume ``tool`` is present on success.
"""

from __future__ import annotations

from fastmcp.tools.base import ToolResult

from clingen_link.exceptions import DataNotFoundError
from clingen_link.mcp.errors import McpErrorContext, run_mcp_tool


def envelope(result: object) -> dict:
    """The flat envelope from either return shape.

    The failure path returns ``ToolResult(structured_content=..., is_error=True)``: the
    protocol flag AND the machine-readable envelope (Response-Envelope v1 §2, issue #46).
    """
    if isinstance(result, ToolResult):
        assert result.is_error is True
        return dict(result.structured_content or {})
    return dict(result)  # type: ignore[call-overload]


async def test_success_envelope_matches_response_envelope_standard_v1() -> None:
    """A dict-returning tool body is banner-wrapped: success + payload + _meta.

    Uses the fleet-canon ``results`` payload key (array of records); the wrapper
    is payload-shape-agnostic and merges the banner around whatever the tool body
    returns, so this also covers the single-item ``result`` (object) variant.
    """

    async def call() -> dict[str, object]:
        return {"results": [{"gene_symbol": "BRCA1"}]}

    result = await run_mcp_tool("search_genes", call)

    assert result["success"] is True
    assert result["results"] == [{"gene_symbol": "BRCA1"}]
    assert result["_meta"]["unsafe_for_clinical_use"] is True


async def test_single_item_result_key_is_preserved() -> None:
    """The single-item ``result`` (object) payload variant is passed through unchanged."""

    async def call() -> dict[str, object]:
        return {"result": {"gene_symbol": "BRCA1"}}

    result = await run_mcp_tool("get_gene_validity", call)

    assert result["success"] is True
    assert result["result"] == {"gene_symbol": "BRCA1"}
    assert result["_meta"]["unsafe_for_clinical_use"] is True


async def test_error_envelope_is_flat_not_a_bare_exception() -> None:
    """An exception raised through the wrapper becomes a flat in-band envelope.

    Never a bare exception, and never the strict nested ``error: {code, message,
    retriable, details}`` shape from the Rules body -- the ratified v1 contract is
    the flat banner: top-level ``error_code``/``retryable``/``recovery_action``.
    """

    async def call() -> dict[str, object]:
        raise DataNotFoundError("BRCA1 not found in the ClinGen snapshot")

    result = await run_mcp_tool(
        "get_gene_validity",
        call,
        context=McpErrorContext(tool_name="get_gene_validity", gene="BRCA1"),
    )

    # v1 REQUIRES the protocol flag too: "isError: true is REQUIRED so clients surface the
    # error to the model for self-correction." A returned dict never sets it (issue #46).
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    result = envelope(result)  # type: ignore[assignment]

    assert result["success"] is False
    assert isinstance(result["error_code"], str) and result["error_code"]
    assert isinstance(result["message"], str) and result["message"]
    assert isinstance(result["retryable"], bool)
    assert isinstance(result["recovery_action"], str) and result["recovery_action"]
    # Flat, not nested: no strict-Rules "error" object anywhere in the payload.
    assert "error" not in result
    assert result["_meta"]["tool"] == "get_gene_validity"
    assert result["_meta"]["unsafe_for_clinical_use"] is True
