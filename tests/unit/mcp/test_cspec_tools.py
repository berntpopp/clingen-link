"""Tests for the four cspec MCP tools (list/get/criterion/search)."""

from __future__ import annotations

import sqlite3

import pytest

from clingen_link.etl import build, cspec_parse, schema
from clingen_link.mcp.service_adapters import ClingenServices, reset_services, set_services
from clingen_link.mcp.tools import cspec as cspec_tools
from clingen_link.store.db import Store


@pytest.fixture(autouse=True)
def _services(tmp_path):
    db = tmp_path / "snap.sqlite"
    conn = sqlite3.connect(db)
    schema.create_schema(conn)
    jsonld = {
        "@id": ".../id/GN092",
        "affiliation": {"@id": ".../id/50087", "label": "ENIGMA"},
        "label": "ENIGMA BRCA1/2 spec",
        "version": "1.1.0",
        "cspecStatus": "Released",
        "ruleSets": [
            {
                "@id": ".../id/9",
                "genes": [
                    {
                        "@id": ".../?query=BRCA1",
                        "diseases": [{"label": "MONDO:0700268"}],
                        "modeOfInheritance": "AD",
                    }
                ],
                "criteriaCodes": [
                    {
                        "@id": ".../id/55",
                        "label": "PVS1",
                        "description": "null variant",
                        "evidenceStrengths": [],
                    }
                ],
            }
        ],
    }
    build._write_cspec(conn, [cspec_parse.parse_spec(jsonld, "", {})])
    conn.commit()
    conn.close()
    set_services(ClingenServices(Store(db), client=None))
    yield
    reset_services()


@pytest.mark.asyncio
async def test_list_cspecs_returns_catalog() -> None:
    out = await cspec_tools._list_cspecs_impl(
        gene=None, affiliation=None, status=None, page=1, size=10, response_mode="compact"
    )
    assert out["success"] is True
    assert out["total"] >= 1
    assert out["records"][0]["gn_id"] == "GN092"
    assert out["_meta"]["unsafe_for_clinical_use"] is True
    nxt = out["_meta"]["next_commands"][0]
    assert nxt["tool"] == "get_cspec"


@pytest.mark.asyncio
async def test_get_cspec_returns_detail() -> None:
    out = await cspec_tools._get_cspec_impl(
        gn_id="GN092", affiliation=None, gene=None, response_mode="compact"
    )
    assert out["success"] is True
    assert out["record"]["criteria"][0]["code"] == "PVS1"
    assert out["_meta"]["unsafe_for_clinical_use"] is True
    nxt = out["_meta"]["next_commands"][0]
    assert nxt["tool"] == "get_cspec_criterion"


@pytest.mark.asyncio
async def test_get_cspec_criterion_by_code() -> None:
    out = await cspec_tools._get_criterion_impl(
        criteria_id=None,
        gn_id="GN092",
        code="PVS1",
        rule_set_id=None,
        response_mode="compact",
    )
    assert out["success"] is True
    assert out["record"]["code"] == "PVS1"
    nxt = out["_meta"]["next_commands"][0]
    assert nxt["tool"] == "get_cspec"


@pytest.mark.asyncio
async def test_get_cspec_not_found() -> None:
    out = await cspec_tools._get_cspec_impl(
        gn_id="GN999", affiliation=None, gene=None, response_mode="compact"
    )
    assert out["success"] is False
    assert out["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_search_cspec() -> None:
    out = await cspec_tools._search_cspec_impl(query="ENIGMA", page=1, size=10)
    assert out["success"] is True
    assert out["total"] >= 1
    assert out["_meta"]["unsafe_for_clinical_use"] is True
