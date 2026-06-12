"""Tests for clingen_link.mcp.schema_relax.relax_output_schema."""

from __future__ import annotations

from clingen_link.mcp.schema_relax import relax_output_schema


def test_strips_required_and_opens_object() -> None:
    schema = {
        "type": "object",
        "properties": {"gene": {"type": "string"}},
        "required": ["gene"],
        "additionalProperties": False,
    }
    relaxed = relax_output_schema(schema)
    assert "required" not in relaxed
    assert relaxed["additionalProperties"] is True


def test_object_without_additional_properties_is_opened() -> None:
    relaxed = relax_output_schema({"type": "object", "properties": {}})
    assert relaxed["additionalProperties"] is True


def test_scalar_type_allows_null() -> None:
    relaxed = relax_output_schema({"type": "object", "properties": {"n": {"type": "integer"}}})
    assert relaxed["properties"]["n"]["type"] == ["integer", "null"]


def test_enum_node_is_not_null_widened() -> None:
    relaxed = relax_output_schema(
        {"type": "object", "properties": {"x": {"type": "string", "enum": ["a", "b"]}}}
    )
    assert relaxed["properties"]["x"]["type"] == "string"


def test_recurses_into_items_and_defs() -> None:
    schema = {
        "type": "object",
        "properties": {
            "rows": {"type": "array", "items": {"type": "object", "required": ["id"]}},
        },
        "$defs": {"Row": {"type": "object", "required": ["id"]}},
    }
    relaxed = relax_output_schema(schema)
    assert "required" not in relaxed["properties"]["rows"]["items"]
    assert "required" not in relaxed["$defs"]["Row"]


def test_non_dict_input_returned_unchanged() -> None:
    assert relax_output_schema(True) is True
    assert relax_output_schema("scalar") == "scalar"
