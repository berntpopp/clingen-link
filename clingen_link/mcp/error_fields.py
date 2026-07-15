"""Flatten a Pydantic/FastMCP validation error into caller-safe ``{field, reason}`` frames.

Split out of :mod:`clingen_link.mcp.errors` for the 600-LOC budget. The security property this
enforces: the raw framework message is NEVER surfaced. It can embed the offending input value
(and control/zero-width/bidi code points), and code-point stripping alone leaves the prose
intact — so the reason is a FIXED string keyed on the error `type`, and the field name is
redacted whenever it is caller-supplied (an unexpected-argument name) rather than one of the
tool's own declared parameters.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError

# Fixed, caller-safe reason per validation-error `type`. Unknown types fall back to a generic
# fixed reason; no branch ever surfaces the raw Pydantic/FastMCP `msg`.
_FIXED_FIELD_REASON: dict[str, str] = {
    "missing": "This required argument is missing.",
    "missing_argument": "This required argument is missing.",
    "extra_forbidden": "This argument is not accepted by the tool.",
    "unexpected_keyword_argument": "This argument is not accepted by the tool.",
    "string_pattern_mismatch": "Value does not match the required format.",
    "string_too_short": "Value is too short.",
    "string_too_long": "Value is too long.",
    "int_parsing": "Value must be an integer.",
    "int_type": "Value must be an integer.",
    "float_parsing": "Value must be a number.",
    "bool_parsing": "Value must be a boolean.",
    "bool_type": "Value must be a boolean.",
    "string_type": "Value must be a string.",
    "greater_than": "Value is below the allowed minimum.",
    "greater_than_equal": "Value is below the allowed minimum.",
    "less_than": "Value is above the allowed maximum.",
    "less_than_equal": "Value is above the allowed maximum.",
    "enum": "Value is not one of the allowed options.",
    "literal_error": "Value is not one of the allowed options.",
    "json_invalid": "Value is not valid JSON.",
}
_GENERIC_FIELD_REASON = "This argument failed validation."
# Types whose `loc` is a caller-supplied (unexpected) argument NAME, which must be
# redacted rather than echoed back.
_CALLER_CONTROLLED_LOC_TYPES = frozenset({"extra_forbidden", "unexpected_keyword_argument"})


def extract_field_errors(errors: list[Any]) -> list[dict[str, str]]:
    """Flatten validation errors into {field, reason} dicts with FIXED, safe values.

    The field NAME is a declared tool-parameter name for the value-validation types
    (safe to echo) but is caller-supplied for the "unexpected argument" types, so those
    are redacted. The reason is always a fixed string keyed on the error type -- the
    raw Pydantic message (which may carry the input value) is never surfaced.
    """
    result: list[dict[str, str]] = []
    for err in errors:
        err_type = str(err.get("type", ""))
        loc = err.get("loc", ())
        if err_type in _CALLER_CONTROLLED_LOC_TYPES:
            field_name = "unknown"
        else:
            field_name = ".".join(str(x) for x in loc) if loc else "unknown"
        reason = _FIXED_FIELD_REASON.get(err_type, _GENERIC_FIELD_REASON)
        result.append({"field": field_name, "reason": reason})
    return result


def pydantic_cause(exc: BaseException) -> PydanticValidationError | None:
    """Walk the ``__cause__``/``__context__`` chain for the pydantic ValidationError.

    FastMCP 3.x re-raises argument-validation failures as
    ``fastmcp.exceptions.ValidationError`` (NOT a pydantic subclass) constructed from the
    pydantic error, so the structured ``.errors()`` -- which we need to build FIXED,
    body-free field reasons -- live on the cause, not on the raised exception itself.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PydanticValidationError):
            return current
        current = current.__cause__ or current.__context__
    return None
