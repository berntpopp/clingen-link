import sqlite3

import pytest

from clingen_link.etl import schema


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()
    return {r[0] for r in rows}


def test_cspec_tables_created() -> None:
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    names = _tables(conn)
    for t in (
        "cspec",
        "cspec_rule_set",
        "cspec_gene",
        "cspec_criteria",
        "cspec_strength",
        "cspec_file",
        "cspec_fts",
        "cspec_search_doc",
    ):
        assert t in names, f"missing table {t}"
    # criteria_id is the PK; code is plain text (collides in multi-ruleset specs).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cspec_criteria)").fetchall()}
    assert {"criteria_id", "rule_set_id", "gn_id", "code", "description", "ord"} <= cols


def test_criteria_id_is_unique_primary_key() -> None:
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    conn.execute(
        "INSERT INTO cspec_criteria (criteria_id, rule_set_id, gn_id, code) VALUES (?,?,?,?)",
        ("1", "10", "GN014", "PM2"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cspec_criteria (criteria_id, rule_set_id, gn_id, code) VALUES (?,?,?,?)",
            ("1", "11", "GN014", "PM2"),
        )


def test_same_gn_id_and_code_allowed_across_rule_sets() -> None:
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    conn.execute(
        "INSERT INTO cspec_criteria (criteria_id, rule_set_id, gn_id, code) VALUES (?,?,?,?)",
        ("100", "10", "GN016", "BS3"),
    )
    # Same (gn_id, code), different criteria_id (different rule set) must be allowed.
    conn.execute(
        "INSERT INTO cspec_criteria (criteria_id, rule_set_id, gn_id, code) VALUES (?,?,?,?)",
        ("200", "11", "GN016", "BS3"),
    )
    rows = conn.execute(
        "SELECT criteria_id FROM cspec_criteria WHERE gn_id = ? AND code = ? ORDER BY criteria_id",
        ("GN016", "BS3"),
    ).fetchall()
    assert [r[0] for r in rows] == ["100", "200"]
