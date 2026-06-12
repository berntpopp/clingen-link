"""Loosen JSON Schemas so success-shaped output schemas don't reject structured error envelopes.

The MCP SDK validates every tool response against the declared output schema. When the tool
returns an error envelope (e.g. {success: false, error_code: "not_found"}), strict success
schemas with required fields reject the envelope and the SDK discards the payload, replacing
it with an opaque "Output validation error" message. Stripping `required` from every schema
keeps the schema's LLM-discovery value (properties still describe expected shape) while
letting error envelopes flow through unchanged.
"""

from __future__ import annotations

from typing import Any

# Scalar JSON Schema type tokens we defensively allow null on. Object/array
# containers are intentionally excluded: null must never stand in for a list or
# dict, or downstream consumers would silently accept a shape regression.
_SCALAR_TYPES = frozenset({"integer", "number", "string", "boolean"})


def relax_output_schema(schema: Any) -> Any:
    """Return a deep-copied schema with `required` stripped and additionalProperties=True.

    Recurses into `properties`, `items`, `$defs`, `definitions`, `oneOf`, `anyOf`, `allOf`.
    Accepts non-dict inputs (e.g. boolean schemas, primitive type hints inside `items`)
    and returns them unchanged.
    """
    if not isinstance(schema, dict):
        return schema  # primitive / boolean schema -> return as-is

    relaxed: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "required":
            continue
        if key == "additionalProperties":
            # Force open. Pydantic emits `additionalProperties: false` for strict models.
            relaxed[key] = True
            continue
        if key == "properties" and isinstance(value, dict):
            relaxed[key] = {k: relax_output_schema(v) for k, v in value.items()}
            continue
        if key == "items":
            if isinstance(value, list):
                relaxed[key] = [relax_output_schema(v) for v in value]
            else:
                relaxed[key] = relax_output_schema(value)
            continue
        if key in ("$defs", "definitions") and isinstance(value, dict):
            relaxed[key] = {k: relax_output_schema(v) for k, v in value.items()}
            continue
        if key in ("oneOf", "anyOf", "allOf") and isinstance(value, list):
            relaxed[key] = [relax_output_schema(v) for v in value]
            continue
        relaxed[key] = value

    # Ensure object schemas accept extra keys (the _meta block we inject).
    if relaxed.get("type") == "object" and "additionalProperties" not in relaxed:
        relaxed["additionalProperties"] = True

    # Defensively allow null on bare scalar types so upstream nulls never trip
    # output-schema validation. Skip any node carrying enum/const (null would
    # pass `type` but fail the independent enum assertion) and never touch
    # container types.
    if "enum" not in relaxed and "const" not in relaxed:
        type_value = relaxed.get("type")
        if isinstance(type_value, str) and type_value in _SCALAR_TYPES:
            relaxed["type"] = [type_value, "null"]
        elif (
            isinstance(type_value, list)
            and type_value
            and "null" not in type_value
            and all(token in _SCALAR_TYPES for token in type_value)
        ):
            relaxed["type"] = [*type_value, "null"]

    return relaxed
