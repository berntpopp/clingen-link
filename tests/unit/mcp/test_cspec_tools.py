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


def _spec_brca1() -> dict:
    """Spec A: GN092 / affiliation 50087 / BRCA1, one PVS1 criterion (id 55)."""
    return {
        "@id": ".../id/GN092",
        "affiliation": {"@id": ".../id/50087", "label": "ENIGMA"},
        "label": "ENIGMA BRCA1 spec",
        "version": "1.1.0",
        "cspecStatus": "Released",
        "currentStatus": "Released",
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
                        "description": "null variant predicted",
                        "evidenceStrengths": [],
                    }
                ],
            }
        ],
    }


def _spec_brca2() -> dict:
    """Spec B: GN093 / same affiliation 50087 / a DIFFERENT gene (BRCA2)."""
    return {
        "@id": ".../id/GN093",
        "affiliation": {"@id": ".../id/50087", "label": "ENIGMA"},
        "label": "ENIGMA BRCA2 spec",
        "version": "1.0.0",
        "cspecStatus": "Released",
        "currentStatus": "Released",
        "ruleSets": [
            {
                "@id": ".../id/12",
                "genes": [
                    {
                        "@id": ".../?query=BRCA2",
                        "diseases": [{"label": "MONDO:0700269"}],
                        "modeOfInheritance": "AD",
                    }
                ],
                "criteriaCodes": [
                    {
                        "@id": ".../id/60",
                        "label": "PVS1",
                        "description": "null variant predicted",
                        "evidenceStrengths": [],
                    }
                ],
            }
        ],
    }


def _spec_multi_rule_set() -> dict:
    """Spec C: GN200 with TWO rule sets, each carrying a PM2 with a DISTINCT criteria_id.

    Both PM2 criteria share the word "population" in their description so an FTS query
    for "population" matches two criterion docs (pagination fodder). The duplicated code
    across rule sets is what makes ``resolve_criterion_ids`` ambiguous (returns 2).
    """
    return {
        "@id": ".../id/GN200",
        "affiliation": {"@id": ".../id/77777", "label": "MyVCEP"},
        "label": "MyVCEP multi-rule-set spec",
        "version": "2.0.0",
        "cspecStatus": "Released",
        "currentStatus": "Released",
        "ruleSets": [
            {
                "@id": ".../id/20",
                "genes": [
                    {
                        "@id": ".../?query=TP53",
                        "diseases": [{"label": "MONDO:0000001"}],
                        "modeOfInheritance": "AD",
                    }
                ],
                "criteriaCodes": [
                    {
                        "@id": ".../id/100",
                        "label": "PM2",
                        "description": "rare absent population frequency",
                        "evidenceStrengths": [],
                    }
                ],
            },
            {
                "@id": ".../id/21",
                "genes": [
                    {
                        "@id": ".../?query=TP53",
                        "diseases": [{"label": "MONDO:0000002"}],
                        "modeOfInheritance": "AR",
                    }
                ],
                "criteriaCodes": [
                    {
                        "@id": ".../id/101",
                        "label": "PM2",
                        "description": "rare absent population frequency",
                        "evidenceStrengths": [],
                    }
                ],
            },
        ],
    }


@pytest.fixture
def _multi_services(tmp_path):
    """A richer snapshot: two specs sharing affiliation 50087 + one multi-rule-set spec.

    Replaces the autouse single-spec snapshot for the branching-logic tests.
    """
    db = tmp_path / "multi.sqlite"
    conn = sqlite3.connect(db)
    schema.create_schema(conn)
    build._write_cspec(
        conn,
        [
            cspec_parse.parse_spec(_spec_brca1(), "", {}),
            cspec_parse.parse_spec(_spec_brca2(), "", {}),
            cspec_parse.parse_spec(_spec_multi_rule_set(), "", {}),
        ],
    )
    conn.commit()
    # Verify the fixture produced the intended rows before any test asserts on them.
    shared = conn.execute(
        "SELECT gn_id FROM cspec WHERE affiliation_id = '50087' ORDER BY gn_id"
    ).fetchall()
    assert [r[0] for r in shared] == ["GN092", "GN093"]
    pm2 = conn.execute(
        "SELECT criteria_id, rule_set_id FROM cspec_criteria "
        "WHERE gn_id = 'GN200' AND code = 'PM2' ORDER BY criteria_id"
    ).fetchall()
    assert [tuple(r) for r in pm2] == [("100", "20"), ("101", "21")]
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


@pytest.mark.asyncio
async def test_get_cspec_multiple_matches_returns_records(_multi_services) -> None:
    """An affiliation covering >1 spec returns the multi-record shape, not a single record."""
    out = await cspec_tools._get_cspec_impl(
        gn_id=None, affiliation="50087", gene=None, response_mode="compact"
    )
    assert out["success"] is True
    # Multi-record branch: ``records`` + ``total`` instead of a single ``record``.
    assert "records" in out
    assert "record" not in out
    assert out["total"] >= 2
    gn_ids = {r["gn_id"] for r in out["records"]}
    assert {"GN092", "GN093"} <= gn_ids


@pytest.mark.asyncio
async def test_get_cspec_criterion_ambiguous_returns_error(_multi_services) -> None:
    """A code shared across rule sets resolves to >1 id -> not_found with a disambiguation hint."""
    out = await cspec_tools._get_criterion_impl(
        criteria_id=None,
        gn_id="GN200",
        code="PM2",
        rule_set_id=None,
        response_mode="compact",
    )
    assert out["success"] is False
    # Same error envelope shape as test_get_cspec_not_found: error_code + message.
    assert out["error_code"] == "not_found"
    message = out["message"]
    assert "rule_set_id" in message
    assert "criteria_id" in message
    assert "disambiguate" in message

    # Supplying one rule_set_id collapses the ambiguity to exactly one criterion.
    resolved = await cspec_tools._get_criterion_impl(
        criteria_id=None,
        gn_id="GN200",
        code="PM2",
        rule_set_id="20",
        response_mode="compact",
    )
    assert resolved["success"] is True
    assert resolved["record"]["code"] == "PM2"
    assert resolved["record"]["criteria_id"] == "100"


@pytest.mark.asyncio
async def test_search_cspec_pagination_truncated(_multi_services) -> None:
    """A size=1 search over multiple FTS hits drops rows and emits the truncation block."""
    out = await cspec_tools._search_cspec_impl(query="population", page=1, size=1)
    assert out["success"] is True
    assert out["total"] > 1
    assert len(out["records"]) == 1
    # Dropped rows surface as _meta["truncated"] (the canonical truncated_block).
    trunc = out["_meta"].get("truncated")
    assert trunc is not None
    assert trunc["kind"] == "pagination"
    assert trunc["dropped"] >= 1


@pytest.mark.asyncio
async def test_get_cspec_compact_trims_verbose_fields(_multi_services) -> None:
    """compact drops _VERBOSE_FIELDS['cspec'] (current_status, affiliation_id); full keeps them.

    Note: ``standard`` ALSO drops these verbose fields (shape_record removes the verbose set in
    both compact and standard); only ``full`` returns them, so ``full`` is asserted here.
    """
    compact = await cspec_tools._get_cspec_impl(
        gn_id="GN092", affiliation=None, gene=None, response_mode="compact"
    )
    record_compact = compact["record"]
    assert "current_status" not in record_compact
    assert "affiliation_id" not in record_compact

    full = await cspec_tools._get_cspec_impl(
        gn_id="GN092", affiliation=None, gene=None, response_mode="full"
    )
    record_full = full["record"]
    assert "current_status" in record_full
    assert "affiliation_id" in record_full
