"""Every closed-vocabulary filter enum must be a SUPERSET of the values the snapshot stores.

An enum NARROWER than the data is a two-way defect (issue #46):
  * a record can RETURN a value the filter enum rejects, so it cannot round-trip as a filter
    (validity `classification` omitted the starred "No Known Disease Relationship*", which the
    ETL preserves verbatim); and
  * filtering by the advertised value silently EXCLUDES the rows carrying the un-enumerated one.

The check is DERIVED FROM THE DATA, not a hand-typed expected set: for each registered closed
field it reads the distinct values the snapshot actually stores and asserts the tool's declared
enum covers them. The registry is the single place a closed field is named — a new one is one
line, and the day a snapshot rebuild introduces an upstream value the enum has not caught up
with, this test fails instead of the server silently dropping rows.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from clingen_link.store import queries
from clingen_link.store.db import Store

# (tool, param) -> the snapshot (table, columns) whose distinct values the enum must cover.
# `assertion` filters adult OR pediatric status, so it spans both columns.
_CLOSED_FIELDS: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("search_validity", "classification", "validity", ("classification",)),
    ("search_validity", "moi", "validity", ("moi",)),
    ("search_dosage", "haplo_score", "dosage", ("haplo_score",)),
    ("search_dosage", "triplo_score", "dosage", ("triplo_score",)),
    ("search_dosage", "record_type", "dosage", ("record_type",)),
    ("get_variant_interpretations", "classification", "erepo", ("assertion",)),
    ("search_actionability", "assertion", "actionability", ("adult_status", "pediatric_status")),
    ("list_cspecs", "status", "cspec", ("cspec_status",)),
]


def _enum_of(prop: dict[str, object]) -> set[str]:
    """The declared closed set for a property, inline or under anyOf (Optional).

    A multi-value ``Literal`` emits ``enum``; a single-value one emits ``const`` — both are
    closed vocabularies and both must be recognised, or a one-value enum reads as "no enum".
    """

    def one(node: dict[str, object]) -> set[str]:
        if isinstance(node.get("enum"), list):
            return {str(v) for v in node["enum"]}  # type: ignore[union-attr]
        if "const" in node:
            return {str(node["const"])}
        return set()

    values = one(prop)
    if values:
        return values
    for branch in prop.get("anyOf") or []:  # type: ignore[union-attr]
        if isinstance(branch, dict):
            values |= one(branch)
    return values


@pytest.mark.asyncio
async def test_every_closed_filter_declares_an_enum(tool_mcp: FastMCP) -> None:
    """None of these filters may be a bare string — an arbitrary value must fail validation."""
    tools = {t.name: t for t in await tool_mcp.list_tools()}
    for tool_name, param, _table, _cols in _CLOSED_FIELDS:
        props = (tools[tool_name].parameters or {}).get("properties", {})
        assert _enum_of(props[param]), f"{tool_name}.{param} is not declared as an enum"


@pytest.mark.asyncio
async def test_closed_enums_are_supersets_of_data(tool_mcp: FastMCP, store: Store) -> None:
    tools = {t.name: t for t in await tool_mcp.list_tools()}
    with store.connection() as conn:
        for tool_name, param, table, columns in _CLOSED_FIELDS:
            declared = _enum_of((tools[tool_name].parameters or {}).get("properties", {})[param])
            stored = queries.distinct_values(conn, table, columns)
            missing = stored - declared
            assert not missing, (
                f"{tool_name}.{param} enum is missing values the snapshot stores: {missing}. "
                "An enum must be a superset of the data (issue #46)."
            )
