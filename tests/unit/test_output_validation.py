"""Tests for clingen_link.mcp.output_validation."""

from __future__ import annotations

import pytest

from clingen_link.mcp.errors import (
    clear_recent_errors,
    clear_recent_schema_drift,
    get_recent_errors,
    get_recent_schema_drift,
)
from clingen_link.mcp.output_validation import (
    _output_validation_field,
    actionable_output_validation_error,
)


@pytest.fixture(autouse=True)
def _reset_rings() -> None:
    clear_recent_errors()
    clear_recent_schema_drift()


def test_actionable_envelope_shape_and_recording() -> None:
    payload = actionable_output_validation_error(
        tool_name="get_gene_validity",
        arguments={"gene": "BRCA1"},
        message="Output validation error: 'classification' is a required property",
    )
    assert payload["success"] is False
    assert payload["error_code"] == "output_validation_failed"
    assert payload["error_field"] == "classification"
    assert payload["_meta"]["unsafe_for_clinical_use"] is True

    # Recorded on both the general error ring and the schema-drift ring.
    assert get_recent_errors()[-1]["error_code"] == "output_validation_failed"
    assert get_recent_schema_drift()[-1]["error_field"] == "classification"


def test_output_validation_field_extraction() -> None:
    assert _output_validation_field("'gene' is a required property") == "gene"
    assert _output_validation_field("some other message") is None


async def test_install_handler_wraps_output_validation_error() -> None:
    import mcp.types

    from clingen_link.mcp.facade import create_clingen_mcp
    from clingen_link.mcp.output_validation import (
        OUTPUT_VALIDATION_PREFIX,
        install_output_validation_error_handler,
    )

    server = create_clingen_mcp()

    # Replace the underlying call-tool handler with one that emits an SDK-style
    # output-validation error, then install our interceptor on top of it.
    async def fake_handler(_request: mcp.types.CallToolRequest) -> mcp.types.ServerResult:
        return mcp.types.ServerResult(
            mcp.types.CallToolResult(
                content=[
                    mcp.types.TextContent(
                        type="text",
                        text=f"{OUTPUT_VALIDATION_PREFIX} 'classification' is a required property",
                    )
                ],
                isError=True,
            )
        )

    server._mcp_server.request_handlers[mcp.types.CallToolRequest] = fake_handler
    install_output_validation_error_handler(server)

    request = mcp.types.CallToolRequest(
        method="tools/call",
        params=mcp.types.CallToolRequestParams(name="get_gene_validity", arguments={}),
    )
    wrapped = server._mcp_server.request_handlers[mcp.types.CallToolRequest]
    result = await wrapped(request)
    payload = result.root.content[0].text
    assert "output_validation_failed" in payload
