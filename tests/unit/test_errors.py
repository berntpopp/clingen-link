"""Tests for clingen_link.mcp.errors: envelope shape and error taxonomy."""

from __future__ import annotations

import pytest

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
    get_recent_errors,
    run_mcp_tool,
)


@pytest.fixture(autouse=True)
def _reset_errors() -> None:
    clear_recent_errors()


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
        (DataNotFoundError("no rows"), "not_found", False),
        (SnapshotUnavailableError("missing"), "snapshot_unavailable", False),
        (UpstreamInputError("bad shape"), "invalid_input", False),
        (RateLimitedError("429"), "rate_limited", True),
        (ClingenApiError("502"), "upstream_unavailable", True),
        (ValueError("bad"), "validation_failed", False),
        (RuntimeError("boom"), "internal_error", False),
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
    assert result["success"] is False
    assert result["error_code"] == expected_code
    assert result["retryable"] is expected_retryable
    assert result["_meta"]["next_commands"][-1]["tool"] == "get_clingen_diagnostics"


async def test_not_found_fallback_uses_gene_context() -> None:
    async def call() -> dict[str, object]:
        raise DataNotFoundError("absent")

    result = await run_mcp_tool(
        "get_gene_validity",
        call,
        context=McpErrorContext(tool_name="get_gene_validity", gene="BRCA1"),
    )
    assert result["fallback_tool"] == "search_genes"
    assert result["fallback_args"] == {"query": "BRCA1"}


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
    assert result["fallback_tool"] == "get_server_capabilities"
    first = result["_meta"]["next_commands"][0]
    assert not (first["tool"] == "search_genes" and first["arguments"].get("query") == "NOPE")


async def test_tool_input_error_message_surfaced() -> None:
    async def call() -> dict[str, object]:
        raise ToolInputError("gene parameter is required")

    result = await run_mcp_tool("get_gene_validity", call)
    assert result["error_code"] == "validation_failed"
    assert result["message"] == "gene parameter is required"


async def test_errors_recorded_in_ring() -> None:
    async def call() -> dict[str, object]:
        raise DataNotFoundError("absent")

    await run_mcp_tool("get_gene_validity", call)
    recent = get_recent_errors()
    assert recent
    assert recent[-1]["error_code"] == "not_found"
