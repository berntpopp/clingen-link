"""Tests for clingen_link.mcp.errors: envelope shape and error taxonomy."""

from __future__ import annotations

import json

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
from clingen_link.mcp.errors import (
    McpErrorContext,
    ToolInputError,
    clear_recent_errors,
    clear_recent_schema_drift,
    get_recent_errors,
    run_mcp_tool,
)
from clingen_link.mcp.output_validation import actionable_output_validation_error
from clingen_link.mcp.untrusted_content import UntrustedTextLimitError


def envelope(result: object) -> dict:
    """The flat envelope, from either return shape.

    The failure path returns ``ToolResult(structured_content=..., is_error=True)`` so the
    call carries the protocol's ``isError`` flag as well as the machine-readable envelope
    (Response-Envelope v1); the success path stays a plain dict.
    """
    if isinstance(result, ToolResult):
        assert result.is_error is True
        return dict(result.structured_content or {})
    return dict(result)  # type: ignore[call-overload]


@pytest.fixture(autouse=True)
def _reset_errors() -> None:
    clear_recent_errors()
    clear_recent_schema_drift()


async def test_success_envelope_adds_success_and_meta() -> None:
    async def call() -> dict[str, object]:
        return {"headline": "ok", "value": 1}

    result = await run_mcp_tool("get_server_capabilities", call)
    assert result["success"] is True
    assert result["_meta"]["unsafe_for_clinical_use"] is True
    assert result["value"] == 1


async def test_success_preserves_existing_meta() -> None:
    async def call() -> dict[str, object]:
        return {"_meta": {"next_commands": [{"tool": "x", "arguments": {}}]}}

    result = await run_mcp_tool("t", call)
    assert result["_meta"]["next_commands"] == [{"tool": "x", "arguments": {}}]
    assert result["_meta"]["unsafe_for_clinical_use"] is True


@pytest.mark.parametrize(
    ("exc", "expected_code", "expected_retryable"),
    [
        # Response-Envelope v1 §2: the codes are a CLOSED enum. `snapshot_unavailable`,
        # `response_too_large`, `validation_failed` and `internal_error` were all outside it
        # and are mapped onto the canon (issue #46); `recovery`/`recovery_action` keep the
        # precise guidance each case needs.
        (DataNotFoundError("no rows"), "not_found", False),
        (SnapshotUnavailableError("missing"), "internal", False),
        (UpstreamInputError("bad shape"), "invalid_input", False),
        (RateLimitedError("429"), "rate_limited", True),
        (ClingenApiError("502"), "upstream_unavailable", True),
        (UntrustedTextLimitError("too big"), "invalid_input", False),
        (ValueError("bad"), "invalid_input", False),
        (RuntimeError("boom"), "internal", False),
    ],
)
async def test_error_classification(
    exc: Exception, expected_code: str, expected_retryable: bool
) -> None:
    async def call() -> dict[str, object]:
        raise exc

    result = await run_mcp_tool(
        "get_gene_validity", call, context=McpErrorContext(tool_name="get_gene_validity")
    )

    env = envelope(result)
    assert env["success"] is False
    assert env["error_code"] == expected_code
    assert env["retryable"] is expected_retryable
    assert env["_meta"]["next_commands"][-1]["tool"] == "get_diagnostics"


async def test_untrusted_text_limit_error_is_typed_and_reformulates() -> None:
    """A v1.1 limit breach surfaces a distinct typed error with reformulate guidance."""

    async def call() -> dict[str, object]:
        raise UntrustedTextLimitError("untrusted object count 9999 exceeds ceiling 128")

    result = await run_mcp_tool(
        "get_gene_validity", call, context=McpErrorContext(tool_name="get_gene_validity")
    )

    env = envelope(result)
    assert env["success"] is False
    # the closed enum has no size-specific code: it is the caller's input to fix.
    assert env["error_code"] == "invalid_input"
    assert env["retryable"] is False
    assert env["recovery_action"] == "reformulate_input"


async def test_not_found_fallback_uses_gene_context() -> None:
    async def call() -> dict[str, object]:
        raise DataNotFoundError("absent")

    result = await run_mcp_tool(
        "get_gene_validity",
        call,
        context=McpErrorContext(tool_name="get_gene_validity", gene="BRCA1"),
    )
    env = envelope(result)
    assert env["fallback_tool"] == "search_genes"
    assert env["fallback_args"] == {"query": "BRCA1"}


async def test_not_found_fallback_avoids_circular_recall() -> None:
    # L3: when the failing query IS the gene (search_genes itself failed), do not re-suggest the
    # identical search_genes call — steer to discovery instead.
    async def call() -> dict[str, object]:
        raise DataNotFoundError("absent")

    result = await run_mcp_tool(
        "search_genes",
        call,
        context=McpErrorContext(tool_name="search_genes", gene="NOPE", query="NOPE"),
    )
    env = envelope(result)
    assert env["fallback_tool"] == "get_server_capabilities"
    first = env["_meta"]["next_commands"][0]
    assert not (first["tool"] == "search_genes" and first["arguments"].get("query") == "NOPE")


async def test_tool_input_error_message_surfaced() -> None:
    async def call() -> dict[str, object]:
        raise ToolInputError("gene parameter is required")

    result = await run_mcp_tool("get_gene_validity", call)

    env = envelope(result)
    assert env["error_code"] == "invalid_input"
    assert env["message"] == "gene parameter is required"


async def test_errors_recorded_in_ring() -> None:
    async def call() -> dict[str, object]:
        raise DataNotFoundError("absent")

    await run_mcp_tool("get_gene_validity", call)
    recent = get_recent_errors()
    assert recent
    assert recent[-1]["error_code"] == "not_found"


async def test_diagnostics_ring_excludes_caller_free_text(mcp: FastMCP) -> None:
    """D2: caller free-text embedded in an exception must never survive into the
    cross-session get_diagnostics ring. The ring stores only non-PII fields
    (tool_name, error_code, exc_type)."""
    sentinel = "SENTINEL-PII-7f3a"

    async def call() -> dict[str, object]:
        raise RuntimeError(f"lookup failed for query={sentinel}")

    # Route the failing call through the error boundary so it lands in the ring.
    await run_mcp_tool("get_gene_validity", call)

    result = await mcp.call_tool("get_diagnostics", {})
    payload = result.structured_content or {}
    assert payload["recent_error_count"] >= 1
    # The sentinel appears NOWHERE in the diagnostics output.
    assert sentinel not in json.dumps(payload)


async def test_schema_drift_ring_excludes_raw_sdk_message(mcp: FastMCP) -> None:
    """D2 (schema-drift ring): the raw SDK output-validation message can embed
    response/query free text, and get_diagnostics surfaces recent_schema_drift
    verbatim to any caller. The ring must retain only non-PII metadata --
    tool_name and the parsed schema error_field (a declared property NAME) --
    never the free-text message tail.
    """
    sentinel = "SENTINEL-DRIFT-9c2b"

    # A realistic SDK message: a legitimate required-property field name
    # ('classification', a declared schema property) plus a free-text tail that
    # carries the caller-derived sentinel. Only the tail must be dropped.
    actionable_output_validation_error(
        tool_name="get_gene_validity",
        arguments={"gene": sentinel},
        message=(
            f"Output validation error: 'classification' is a required property; "
            f"received query={sentinel}"
        ),
    )

    result = await mcp.call_tool("get_diagnostics", {})
    payload = result.structured_content or {}
    assert payload["recent_schema_drift_count"] >= 1
    # The parsed, safe schema field survives...
    assert payload["recent_schema_drift"][-1]["error_field"] == "classification"
    # ...but the free-text sentinel appears NOWHERE in the diagnostics output.
    assert sentinel not in json.dumps(payload)
