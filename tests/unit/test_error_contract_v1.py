"""Response-Envelope Standard v1: the error frame a client actually branches on.

Three invariants, all of which clingen-link violated (issue #46):

1. **``isError: true``** — a returned dict never sets it, so a client branching on the
   MCP protocol flag saw every structured error as a *successful call*. The only shape
   that carries both the flag and the machine-readable envelope is
   ``ToolResult(structured_content=..., is_error=True)``; ``raise`` throws the envelope away.
2. **``error_code`` is a closed enum** — ``invalid_input · not_found · ambiguous_query ·
   upstream_unavailable · rate_limited · internal``. Anything else (``validation_failed``,
   ``internal_error``, ``snapshot_unavailable``, ``output_validation_failed``) is a violation,
   however sensible it reads.
3. **A bad argument is ``invalid_input``, never ``not_found``**, and the message must name a
   parameter — ``not_found`` tells the model the TOOL does not exist, so it strikes it from
   its list and never calls it again.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult

from clingen_link.exceptions import (
    ClingenApiError,
    DataNotFoundError,
    RateLimitedError,
    SnapshotUnavailableError,
    UpstreamInputError,
)
from clingen_link.mcp.errors import ERROR_CODES, McpErrorContext, run_mcp_tool
from clingen_link.mcp.untrusted_content import UntrustedTextLimitError

# Response-Envelope Standard v1 §2. The gate (docs/conformance/behaviour.py) asserts the same set.
_CLOSED_ENUM = {
    "invalid_input",
    "not_found",
    "ambiguous_query",
    "upstream_unavailable",
    "rate_limited",
    "internal",
}


def test_the_modules_enum_is_the_standards_enum() -> None:
    assert ERROR_CODES == _CLOSED_ENUM


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (DataNotFoundError("gone"), "not_found"),
        (UpstreamInputError("bad id"), "invalid_input"),
        (RateLimitedError("429"), "rate_limited"),
        (ValueError("bad"), "invalid_input"),
        (UntrustedTextLimitError("too big"), "invalid_input"),
        (ClingenApiError("upstream down"), "upstream_unavailable"),
        (TimeoutError("slow"), "upstream_unavailable"),
        (SnapshotUnavailableError("no snapshot"), "internal"),
        (RuntimeError("boom"), "internal"),
    ],
)
async def test_every_classified_exception_lands_in_the_closed_enum(
    exc: BaseException, expected: str
) -> None:
    async def call() -> dict[str, object]:
        raise exc

    result = await run_mcp_tool("t", call, context=McpErrorContext(tool_name="t"))

    assert isinstance(result, ToolResult)
    envelope = result.structured_content or {}
    assert envelope["error_code"] == expected
    assert envelope["error_code"] in _CLOSED_ENUM


async def test_an_error_envelope_sets_the_protocol_is_error_flag() -> None:
    """The fleet's most widespread protocol violation: success:false with isError:false."""

    async def call() -> dict[str, object]:
        raise DataNotFoundError("no such gene")

    result = await run_mcp_tool("get_gene_dosage", call)

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    # ...and the structured envelope survives (a bare `raise` would discard it).
    envelope = result.structured_content or {}
    assert envelope["success"] is False
    assert envelope["error_code"] == "not_found"
    assert envelope["_meta"]["unsafe_for_clinical_use"] is True


async def test_a_success_envelope_is_not_an_error() -> None:
    async def call() -> dict[str, object]:
        return {"records": []}

    result = await run_mcp_tool("search_dosage", call)

    assert not isinstance(result, ToolResult)
    assert result["success"] is True


@pytest.mark.asyncio
class TestArgumentErrorsAreActionable:
    async def test_an_unknown_argument_is_invalid_input_and_names_the_parameters(
        self, tool_mcp: FastMCP
    ) -> None:
        result = await tool_mcp.call_tool("get_gene_dosage", {"__no_such_arg__": "x"})
        envelope = result.structured_content or {}

        assert result.is_error is True
        assert envelope["error_code"] == "invalid_input"
        # The model has to be able to self-correct: name the parameters it MAY pass.
        message = str(envelope["message"]) + str(envelope.get("recovery"))
        assert "gene_symbol" in message

    async def test_an_out_of_enum_value_is_invalid_input(self, tool_mcp: FastMCP) -> None:
        result = await tool_mcp.call_tool(
            "get_gene_dosage", {"gene_symbol": "AAGAB", "response_mode": "__nope__"}
        )
        envelope = result.structured_content or {}

        assert result.is_error is True
        assert envelope["error_code"] == "invalid_input"
        assert "response_mode" in str(envelope["message"]) + str(envelope.get("recovery"))

    async def test_a_missing_required_argument_is_invalid_input(self, tool_mcp: FastMCP) -> None:
        result = await tool_mcp.call_tool("get_gene_dosage", {})
        envelope = result.structured_content or {}

        assert result.is_error is True
        assert envelope["error_code"] == "invalid_input"

    async def test_an_unresolvable_gene_is_still_not_found(self, tool_mcp: FastMCP) -> None:
        """The counterpart: a well-formed identifier that does not exist IS not_found."""
        result = await tool_mcp.call_tool("get_gene_dosage", {"gene_symbol": "ZZZNOPE"})
        envelope = result.structured_content or {}

        assert result.is_error is True
        assert envelope["error_code"] == "not_found"
