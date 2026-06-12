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
