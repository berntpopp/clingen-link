import sqlite3

import pytest

from clingen_link.etl import build, cspec_parse, schema
from clingen_link.store import cspec_queries


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    schema.create_schema(c)
    jsonld = {
        "@id": ".../id/GN164",
        "affiliation": {"@id": ".../id/50140", "label": "ABCA4 VCEP"},
        "label": "ABCA4 spec",
        "version": "1.0.0",
        "cspecStatus": "Released",
        "ruleSets": [
            {
                "@id": ".../id/777",
                "genes": [
                    {
                        "@id": ".../?query=ABCA4",
                        "diseases": [{"label": "MONDO:0800406"}],
                        "modeOfInheritance": "AR",
                    }
                ],
                "criteriaCodes": [
                    {
                        "@id": ".../id/538211541",
                        "label": "BS3",
                        "description": "no damaging effect",
                        "evidenceStrengths": [
                            {"label": "Supporting", "applicability": "Applicable"}
                        ],
                    }
                ],
            }
        ],
    }
    build._write_cspec(c, [cspec_parse.parse_spec(jsonld, "", {})])
    c.commit()
    return c


def test_get_cspec_by_gn(conn) -> None:
    spec = cspec_queries.get_cspec_by_gn(conn, "GN164")
    assert spec is not None and spec["affiliation_label"] == "ABCA4 VCEP"


def test_get_criteria_and_criterion(conn) -> None:
    crit = cspec_queries.get_criteria(conn, "GN164")
    assert crit[0]["code"] == "BS3"
    one = cspec_queries.get_criterion(conn, "538211541")
    assert one is not None and one["strengths"][0]["strength_label"] == "Supporting"


def test_list_and_search(conn) -> None:
    rows, total = cspec_queries.list_cspecs(conn, gene="ABCA4")
    assert total == 1 and rows[0]["gn_id"] == "GN164"
    hits, htotal = cspec_queries.search_cspec(conn, text="damaging")
    assert htotal >= 1 and hits[0]["entity_type"] == "criterion"


def test_resolve_affiliation_gene(conn) -> None:
    assert cspec_queries.resolve_gn(conn, affiliation_id="50140", gene="ABCA4") == ["GN164"]


def _build(c: sqlite3.Connection, *specs: dict) -> None:
    """Parse + write one or more spec JSON-LD docs into the in-memory snapshot."""
    parsed = [cspec_parse.parse_spec(s, "", {}) for s in specs]
    build._write_cspec(c, parsed)
    c.commit()


def _spec(
    gn_id: str,
    *,
    affiliation_id: str,
    affiliation_label: str,
    status: str,
    rule_sets: list[dict],
) -> dict:
    return {
        "@id": f".../id/{gn_id}",
        "affiliation": {"@id": f".../id/{affiliation_id}", "label": affiliation_label},
        "label": f"{gn_id} spec",
        "version": "1.0.0",
        "cspecStatus": status,
        "ruleSets": rule_sets,
    }


@pytest.fixture
def empty_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    schema.create_schema(c)
    return c


def test_resolve_criterion_multi_rule_set(empty_conn) -> None:
    """One spec, two rule sets, both with a 'PM2' code but distinct criteria_id."""
    spec = _spec(
        "GN014",
        affiliation_id="50100",
        affiliation_label="Multi VCEP",
        status="Released",
        rule_sets=[
            {
                "@id": ".../id/111",
                "genes": [{"@id": ".../?query=GENEA", "modeOfInheritance": "AD"}],
                "criteriaCodes": [
                    {"@id": ".../id/1001", "label": "PM2", "description": "rare in set one"}
                ],
            },
            {
                "@id": ".../id/222",
                "genes": [{"@id": ".../?query=GENEB", "modeOfInheritance": "AR"}],
                "criteriaCodes": [
                    {"@id": ".../id/2002", "label": "PM2", "description": "rare in set two"}
                ],
            },
        ],
    )
    _build(empty_conn, spec)

    both = cspec_queries.resolve_criterion(empty_conn, "GN014", "PM2")
    assert sorted(both) == ["1001", "2002"]
    assert len(both) == 2

    narrowed = cspec_queries.resolve_criterion(empty_conn, "GN014", "PM2", rule_set_id="111")
    assert narrowed == ["1001"]
    assert len(narrowed) == 1


def test_search_cspec_pagination(empty_conn) -> None:
    """Three criteria share the word 'widget' -> total exceeds a size-1 page."""
    spec = _spec(
        "GN300",
        affiliation_id="50300",
        affiliation_label="Search VCEP",
        status="Released",
        rule_sets=[
            {
                "@id": ".../id/900",
                "genes": [{"@id": ".../?query=GENEX", "modeOfInheritance": "AD"}],
                "criteriaCodes": [
                    {"@id": ".../id/3001", "label": "PM2", "description": "widget alpha"},
                    {"@id": ".../id/3002", "label": "PS3", "description": "widget beta"},
                    {"@id": ".../id/3003", "label": "BS3", "description": "widget gamma"},
                ],
            }
        ],
    )
    _build(empty_conn, spec)

    hits_p1, total = cspec_queries.search_cspec(empty_conn, text="widget", page=1, size=1)
    assert total == 3
    assert len(hits_p1) == 1

    hits_p2, total2 = cspec_queries.search_cspec(empty_conn, text="widget", page=2, size=1)
    assert total2 == 3
    assert len(hits_p2) == 1
    # Different page slices return different rows (ordered by rowid).
    assert hits_p1[0]["criteria_id"] != hits_p2[0]["criteria_id"]


def test_list_cspecs_filters_and_pagination(empty_conn) -> None:
    """Two specs, different affiliations + statuses: filter and paginate."""
    spec_a = _spec(
        "GN401",
        affiliation_id="50401",
        affiliation_label="Alpha VCEP",
        status="Released",
        rule_sets=[
            {
                "@id": ".../id/410",
                "genes": [{"@id": ".../?query=GENEA", "modeOfInheritance": "AD"}],
                "criteriaCodes": [{"@id": ".../id/4001", "label": "PM2", "description": "a"}],
            }
        ],
    )
    spec_b = _spec(
        "GN402",
        affiliation_id="50402",
        affiliation_label="Beta VCEP",
        status="In Progress",
        rule_sets=[
            {
                "@id": ".../id/420",
                "genes": [{"@id": ".../?query=GENEB", "modeOfInheritance": "AR"}],
                "criteriaCodes": [{"@id": ".../id/4002", "label": "PS3", "description": "b"}],
            }
        ],
    )
    _build(empty_conn, spec_a, spec_b)

    # Filter by affiliation -> only the matching spec.
    rows, total = cspec_queries.list_cspecs(empty_conn, affiliation="50401")
    assert total == 1
    assert [r["gn_id"] for r in rows] == ["GN401"]

    # Filter by status -> only the matching spec.
    rows_s, total_s = cspec_queries.list_cspecs(empty_conn, status="In Progress")
    assert total_s == 1
    assert [r["gn_id"] for r in rows_s] == ["GN402"]

    # Unfiltered pagination: total is the full count, page slices the rows.
    page1, total_all = cspec_queries.list_cspecs(empty_conn, page=1, size=1)
    assert total_all == 2
    assert [r["gn_id"] for r in page1] == ["GN401"]

    page2, total_all2 = cspec_queries.list_cspecs(empty_conn, page=2, size=1)
    assert total_all2 == 2
    assert [r["gn_id"] for r in page2] == ["GN402"]
