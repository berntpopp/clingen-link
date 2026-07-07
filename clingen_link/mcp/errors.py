"""Structured MCP error envelopes for clingen-link tools.

Patterned after the house-style error envelope (originally pubtator-link).
The envelope shape is what LLMs branch on; codes are deterministic per exception
class so prompts can recover without scraping free text.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from clingen_link.config import settings
from clingen_link.exceptions import (
    ClingenApiError,
    DataNotFoundError,
    RateLimitedError,
    SnapshotUnavailableError,
    UpstreamInputError,
)

logger = logging.getLogger(__name__)

RECENT_MCP_ERROR_LIMIT = 50
_RECENT_ERRORS: deque[dict[str, Any]] = deque(maxlen=RECENT_MCP_ERROR_LIMIT)

# Schema-drift events live in a separate, smaller ring so LLM callers can
# distinguish business errors (the general ring) from infrastructure events
# such as upstream payloads no longer matching our declared output_schema.
RECENT_SCHEMA_DRIFT_LIMIT = 25
_RECENT_SCHEMA_DRIFT: deque[dict[str, Any]] = deque(maxlen=RECENT_SCHEMA_DRIFT_LIMIT)

# Base `_meta` block merged into every success and error envelope.
_BASE_META: dict[str, Any] = {
    "unsafe_for_clinical_use": True,
}

# Fallback tool used in validation and output-validation error envelopes.
# Points to get_diagnostics for rich health context on error recovery.
_FALLBACK_TOOL = "get_diagnostics"


@dataclass
class McpErrorContext:
    """Per-call context passed to the error builder so envelopes can suggest fallbacks."""

    tool_name: str
    gene: str | None = None
    disease: str | None = None
    mondo: str | None = None
    caid: str | None = None
    hgvs: str | None = None
    query: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class McpToolError(Exception):
    """An exception whose `str(self)` is the JSON-serialised envelope."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(json.dumps(payload))
        self.payload = payload


class ToolInputError(ValueError):
    """A local, pre-upstream validation failure whose message is developer-authored.

    A bare ``ValueError`` may carry raw user input, so its message is redacted in
    the envelope. The strings raised by our own guard sites contain no user
    VALUES -- only static guidance or parameter NAMES -- so a ``ToolInputError``
    message is safe to surface verbatim (capped by ``_safe_message``). It still
    classifies as ``validation_failed`` because it subclasses ``ValueError``.
    """


def _provenance_meta(context: McpErrorContext | None = None) -> dict[str, Any]:
    """Base ``_meta`` provenance merged into every success and error envelope.

    Always carries the research-use flag. Later phases extend this with the
    snapshot data version once the store is wired in.
    """
    meta: dict[str, Any] = dict(_BASE_META)
    return meta


def _safe_message(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    # ClinGen errors are user-input shaped; trim long tracebacks/identifiers.
    return text[:240]


def _fallback_for(context: McpErrorContext) -> tuple[str, dict[str, Any] | None]:
    """Resolve the context-appropriate resolver tool for not_found / invalid_input.

    Gene/disease/variant tools point at ``search_genes`` (the hub entrypoint) with the gene the
    caller supplied — but never with the *exact* query that just failed, since re-running
    ``search_genes`` with an identical failing query is a no-op loop (assessment L3). When the gene
    and the failing query are the same value (i.e. ``search_genes`` itself failed to resolve), steer
    to discovery instead so the LLM makes forward progress.
    """
    if context.gene and context.gene != context.query:
        return "search_genes", {"query": context.gene}
    return "get_server_capabilities", None


def _classify(
    exc: BaseException, context: McpErrorContext
) -> tuple[str, bool, str | None, dict[str, Any] | None]:
    """Return (error_code, retryable, fallback_tool, fallback_args).

    Subclass ordering matters: DataNotFoundError, UpstreamInputError, and
    RateLimitedError all subclass ClingenApiError, so they MUST be checked before
    the generic ClingenApiError branch or they fall through to the (retryable)
    upstream_unavailable bucket. The load-bearing invariant: retryable=true means
    an identical call may later succeed; false means it never will.
    """
    if isinstance(exc, DataNotFoundError):
        tool, args = _fallback_for(context)
        return "not_found", False, tool, args
    if isinstance(exc, SnapshotUnavailableError):
        # The bundled snapshot is missing/unreadable. The caller cannot fix this;
        # steer to diagnostics so the operator can run `clingen-link refresh`.
        return "snapshot_unavailable", False, "get_diagnostics", {}
    if isinstance(exc, UpstreamInputError):
        # Deterministic upstream rejection (wrong id shape). Retrying unchanged
        # can never succeed.
        tool, args = _fallback_for(context)
        return "invalid_input", False, tool, args
    if isinstance(exc, RateLimitedError):
        return "rate_limited", True, "get_diagnostics", {}
    if isinstance(exc, ValueError):
        return "validation_failed", False, "get_server_capabilities", None
    if isinstance(exc, ClingenApiError):
        return "upstream_unavailable", True, "get_diagnostics", {}
    if isinstance(exc, TimeoutError):
        return "upstream_unavailable", True, "get_diagnostics", {}
    return "internal_error", False, "get_diagnostics", {}


def _recovery_action(error_code: str, retryable: bool) -> str:
    """Action-typed guidance so the LLM does not infer behavior from a bare bool.

    retry_backoff (wait + retry same call) | reformulate_input (fix the id/fields,
    same tool) | switch_tool (call the fallback_tool, then the original).
    """
    if retryable:
        return "retry_backoff"
    if error_code in {"invalid_input", "validation_failed"}:
        return "reformulate_input"
    return "switch_tool"


def _recovery_text(error_code: str, fallback_tool: str | None, tool_name: str | None = None) -> str:
    if error_code == "not_found":
        resolver = fallback_tool or "search_genes"
        return (
            "Identifier well-formed but absent in the ClinGen snapshot. This is a "
            "reformulate, not a retry: confirm the gene/disease/variant identifier "
            f"or call {resolver} to resolve free text into a canonical identifier."
        )
    if error_code == "invalid_input":
        resolver = fallback_tool or "get_server_capabilities"
        return (
            "The request was rejected as malformed (the identifier or query shape "
            "is wrong for this tool). Do not retry unchanged. Reformulate the "
            f"identifier or call {resolver} to convert free text into the required id."
        )
    if error_code == "rate_limited":
        floor = settings.queue_wait_timeout_s
        return (
            "Upstream rate limit (HTTP 429) or local concurrency saturation. Safe to "
            f"retry after backing off exponentially (start around {floor}s) and reduce "
            "the number of concurrent calls to this server."
        )
    if error_code == "validation_failed":
        return (
            "Inputs failed validation. Check the tool schema and call "
            "get_server_capabilities for accepted identifier shapes and filters."
        )
    if error_code == "snapshot_unavailable":
        return (
            "The bundled ClinGen snapshot is missing or unreadable. The operator "
            "must run `clingen-link refresh` to (re)build it. Call "
            "get_diagnostics for snapshot freshness details."
        )
    if error_code == "upstream_unavailable":
        return (
            "A ClinGen upstream endpoint failed transiently. Safe to retry with "
            "exponential backoff (cap attempts)."
        )
    return (
        f"Unexpected failure. Call {fallback_tool} for a safe entry point."
        if fallback_tool
        else "Unexpected failure."
    )


def _envelope_message(exc: BaseException, error_code: str) -> str:
    """Return a message safe to surface to LLM callers.

    Validation errors use a canned prefix so callers can pattern-match without
    receiving raw user input. Internal errors are fully opaque to avoid leaking
    implementation details or sensitive values.
    """
    if isinstance(exc, ToolInputError):
        # Developer-authored guard string (static or parameter NAMES only, no user
        # values), so it is safe to surface verbatim instead of redacting.
        return _safe_message(exc)
    if error_code == "validation_failed":
        return f"Invalid input: {exc.__class__.__name__}"
    if error_code == "internal_error":
        return f"Internal error: {exc.__class__.__name__}"
    return _safe_message(exc)


def _extract_field_errors(errors: list[Any]) -> list[dict[str, str]]:
    """Flatten Pydantic validation errors into {field, reason} dicts."""
    result: list[dict[str, str]] = []
    for err in errors:
        loc = err.get("loc", ())
        field_name = ".".join(str(x) for x in loc) if loc else "unknown"
        reason = err.get("msg", str(err.get("type", "invalid")))
        result.append({"field": field_name, "reason": reason})
    return result


def mcp_validation_tool_error(
    *,
    tool_name: str,
    exc: PydanticValidationError,
) -> McpToolError:
    """Build a sanitized validation failure raised before tool execution starts."""
    field_errors = _extract_field_errors(list(exc.errors()))
    payload: dict[str, Any] = {
        "success": False,
        "error_code": "validation_failed",
        "message": "Invalid MCP arguments.",
        "retryable": False,
        "recovery_action": "reformulate_input",
        "fallback_tool": _FALLBACK_TOOL,
        "fallback_args": {},
        "field_errors": field_errors,
        "recovery": (
            "Inputs failed validation. Check field_errors for details and call "
            f"{_FALLBACK_TOOL} for accepted identifier shapes and filters."
        ),
        "_meta": {
            "next_commands": [{"tool": _FALLBACK_TOOL, "arguments": {}}],
            **_provenance_meta(),
        },
    }
    return McpToolError(payload)


def install_validation_error_handler(mcp_server: Any) -> None:
    """Wrap registered tools so FastMCP argument validation returns our envelope.

    FastMCP stores tools on ``_local_provider._components`` (modern path) or the
    legacy ``_tool_manager._tools`` mapping. We probe both so the handler keeps
    working across FastMCP minor versions. Tools without a ``run`` method (e.g.
    resources or prompts that happen to share the registry) are skipped.
    """
    candidates: list[Any] = []
    local_provider = getattr(mcp_server, "_local_provider", None)
    components = getattr(local_provider, "_components", None)
    if isinstance(components, dict):
        candidates.extend(components.values())
    tool_manager = getattr(mcp_server, "_tool_manager", None)
    legacy_tools = getattr(tool_manager, "_tools", None)
    if isinstance(legacy_tools, dict):
        candidates.extend(legacy_tools.values())

    for tool in candidates:
        if not hasattr(tool, "run") or getattr(tool, "_clingen_validation_wrapped", False):
            continue
        original_run = tool.run

        async def wrapped_run(
            arguments: dict[str, Any],
            *,
            _original_run: Callable[[dict[str, Any]], Awaitable[Any]] = original_run,
            _tool: Any = tool,
        ) -> Any:
            try:
                return await _original_run(arguments)
            except PydanticValidationError as exc:
                envelope = mcp_validation_tool_error(
                    tool_name=str(getattr(_tool, "name", "unknown")),
                    exc=exc,
                ).payload
                record_mcp_error(
                    tool_name=str(getattr(_tool, "name", "unknown")),
                    error_code="validation_failed",
                    exc_type=type(exc).__name__,
                )
                convert_result = getattr(_tool, "convert_result", None)
                if callable(convert_result):
                    return convert_result(envelope)
                return envelope

        object.__setattr__(tool, "run", wrapped_run)
        object.__setattr__(tool, "_clingen_validation_wrapped", True)


def mcp_tool_error(exc: BaseException, context: McpErrorContext) -> McpToolError:
    error_code, retryable, fallback_tool, fallback_args = _classify(exc, context)
    # next_commands must agree with the classified fallback: prepend the
    # task-advancing resolver when there is one, keeping diagnostics as the
    # secondary entry. For retryable codes fallback_tool is already diagnostics,
    # so the guard collapses to a single diagnostics entry (retry, not switch).
    next_commands: list[dict[str, Any]] = []
    if fallback_tool and fallback_tool != _FALLBACK_TOOL:
        next_commands.append({"tool": fallback_tool, "arguments": fallback_args or {}})
    next_commands.append({"tool": _FALLBACK_TOOL, "arguments": {}})
    payload = {
        "success": False,
        "error_code": error_code,
        "message": _envelope_message(exc, error_code),
        "retryable": retryable,
        "recovery_action": _recovery_action(error_code, retryable),
        "fallback_tool": fallback_tool,
        "fallback_args": fallback_args,
        "recovery": _recovery_text(error_code, fallback_tool, context.tool_name),
        "_meta": {
            "tool": context.tool_name,
            "next_commands": next_commands,
            **_provenance_meta(context),
        },
    }
    return McpToolError(payload)


def record_mcp_error(*, tool_name: str, error_code: str, exc_type: str) -> None:
    """Append a non-PII summary of a failed call to the cross-session ring.

    The ring is surfaced verbatim by ``get_diagnostics`` to any caller, so it must
    never store the exception's free text: a raw ``str(exc)`` or envelope message
    can embed the caller's query and leak it across sessions. Only the tool name,
    the deterministic ``error_code``, and the exception class name are retained --
    enough to self-diagnose without exposing input values. The full exception class
    is still emitted on the structured LOG line for operators.
    """
    _RECENT_ERRORS.append(
        {
            "tool_name": tool_name,
            "error_code": error_code,
            "exc_type": exc_type,
        }
    )


def get_recent_errors() -> list[dict[str, Any]]:
    return list(_RECENT_ERRORS)


def clear_recent_errors() -> None:
    _RECENT_ERRORS.clear()


def record_schema_drift(*, tool_name: str, error_field: str | None) -> None:
    """Append an output-schema-drift event to the bounded ring.

    Separate from record_mcp_error so an LLM (via get_diagnostics) can
    distinguish business errors from infrastructure events (the upstream payload
    no longer matches our declared output_schema).

    Like the recent-errors ring, this is surfaced verbatim by ``get_diagnostics``
    to any caller, so it must never store the raw SDK validation message: that
    string can embed response values or the caller's query and would leak across
    sessions. Only ``tool_name`` and the parsed ``error_field`` -- a declared
    schema property NAME, never a value -- are retained. The raw message is still
    available to operators on the structured LOG line at the call site.
    """
    _RECENT_SCHEMA_DRIFT.append(
        {
            "tool_name": tool_name,
            "error_field": error_field,
        }
    )


def get_recent_schema_drift() -> list[dict[str, Any]]:
    return list(_RECENT_SCHEMA_DRIFT)


def clear_recent_schema_drift() -> None:
    _RECENT_SCHEMA_DRIFT.clear()


async def run_mcp_tool(
    tool_name: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    context: McpErrorContext | None = None,
) -> dict[str, Any]:
    """Execute an MCP tool body, converting any exception to an envelope dict.

    Returning the envelope (rather than raising) is what pubtator-link does so
    that the LLM sees a structured failure instead of an `isError: true` MCP
    response with an opaque message.
    """
    ctx = context or McpErrorContext(tool_name=tool_name)
    try:
        result = await call()
        # Inject research-use meta into every successful dict response unless the
        # tool already provides _meta. A symmetric success:true flag lets callers
        # branch on `success` instead of special-casing `is False`.
        if isinstance(result, dict):
            result.setdefault("success", True)
            existing_meta: dict[str, Any] = result.get("_meta") or {}
            result["_meta"] = {**existing_meta, **_provenance_meta(ctx)}
        return result
    except McpToolError as exc:
        record_mcp_error(
            tool_name=tool_name,
            error_code=exc.payload.get("error_code", "internal_error"),
            exc_type=type(exc).__name__,
        )
        return exc.payload
    except Exception as exc:  # broad catch is the error-boundary contract
        wrapped = mcp_tool_error(exc, ctx)
        logger.warning(
            "mcp_tool_error tool=%s code=%s exc=%s",
            tool_name,
            wrapped.payload["error_code"],
            exc.__class__.__name__,
        )
        record_mcp_error(
            tool_name=tool_name,
            error_code=wrapped.payload["error_code"],
            exc_type=type(exc).__name__,
        )
        return wrapped.payload
