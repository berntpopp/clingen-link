import sqlite3

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
