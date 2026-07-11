"""JSON-Schema fragments that declare the v1.1 ``untrusted_text`` object in tool output.

Response-Envelope Standard v1.1 requires each fenced free-text field — including one
nested inside an array's ``items`` — to declare the typed ``untrusted_text`` object with
the ``kind`` literal in the tool's ``output_schema`` (a bare permissive ``object`` hides
the literal and is non-conformant even when the runtime data is fenced).

These fragments are composed into each tool's success schema and then passed through
``relax_output_schema`` (which preserves ``const``/``enum`` and recurses ``anyOf`` /
``items`` / ``properties``), so the ``kind`` const survives while the schema stays
error-envelope tolerant. Optional fenced fields use the ``*_OR_NULL`` form because a
record may legitimately carry ``null`` (e.g. a criterion with no description) in the
standard/full response modes that keep nulls.
"""

from __future__ import annotations

# One fenced external-prose object: {kind, text, provenance, raw_sha256}.
UNTRUSTED_TEXT_SCHEMA: dict[str, object] = {
    "type": "object",
    "description": "Response-Envelope v1.1 fenced external prose (opaque untrusted_text object).",
    "properties": {
        "kind": {"const": "untrusted_text"},
        "text": {"type": "string"},
        "provenance": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "record_id": {"type": "string"},
                "retrieved_at": {"type": "string"},
            },
        },
        "raw_sha256": {"type": "string"},
    },
}

# A fenced field that may be absent-as-null in the null-preserving response modes.
UNTRUSTED_TEXT_OR_NULL: dict[str, object] = {"anyOf": [UNTRUSTED_TEXT_SCHEMA, {"type": "null"}]}


def record_items(*fenced_fields: str) -> dict[str, object]:
    """An array ``items`` object schema declaring each named field as fenced-or-null."""
    return {
        "type": "object",
        "properties": dict.fromkeys(fenced_fields, UNTRUSTED_TEXT_OR_NULL),
    }


# One CSpec criterion: its own spec text + each evidence-strength's spec text are fenced.
CRITERION_ITEMS: dict[str, object] = {
    "type": "object",
    "properties": {
        "description": UNTRUSTED_TEXT_OR_NULL,
        "strengths": {"type": "array", "items": record_items("description")},
    },
}

# The cspec ``record`` key carries EITHER a single criterion (get_cspec_criterion:
# description + strengths) OR a full spec detail (get_cspec: criteria[]). Declaring all
# three keys is harmless — the inapplicable ones are simply absent on the other shape.
CSPEC_RECORD_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "description": UNTRUSTED_TEXT_OR_NULL,
        "strengths": {"type": "array", "items": record_items("description")},
        "criteria": {"type": "array", "items": CRITERION_ITEMS},
    },
}
