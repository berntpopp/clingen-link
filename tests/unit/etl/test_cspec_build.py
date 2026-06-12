import sqlite3

from clingen_link.etl import build, cspec_parse, schema


def _spec_inputs():
    jsonld = {
        "@id": ".../id/GN164",
        "affiliation": {"@id": ".../id/50140", "label": "ABCA4 VCEP"},
        "label": "ABCA4 spec v1", "version": "1.0.0", "cspecStatus": "Released",
        "ruleSets": [{
            "@id": ".../id/777",
            "genes": [{"@id": ".../?query=ABCA4",
                       "diseases": [{"label": "MONDO:0800406"}], "modeOfInheritance": "AR"}],
            "criteriaCodes": [{"@id": ".../id/538211541", "label": "BS3",
                               "description": "no damaging effect",
                               "evidenceStrengths": [{"label": "Supporting",
                                                      "applicability": "Applicable"}]}],
        }],
    }
    html = '<h3>BS3</h3><a href="/cspec/File/id/abc/data">x</a>'
    heads = {"https://cspec.genome.network/cspec/File/id/abc/data":
             {"content-disposition": "filename=ABCA4-BS3.xlsx", "content-type": "x",
              "content-length": "5"}}
    return cspec_parse.parse_spec(jsonld, html, heads)


def test_write_cspec_populates_tables_and_fts() -> None:
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    count = build._write_cspec(conn, [_spec_inputs()])
    assert count == 1
    assert conn.execute("SELECT code FROM cspec_criteria").fetchone()[0] == "BS3"
    assert conn.execute("SELECT filename FROM cspec_file").fetchone()[0] == "ABCA4-BS3.xlsx"
    # FTS resolves to the criterion entity via the row map.
    rid = conn.execute(
        "SELECT rowid FROM cspec_fts WHERE cspec_fts MATCH ?", ('"BS3"',)
    ).fetchone()[0]
    doc = conn.execute(
        "SELECT entity_type, gn_id FROM cspec_search_doc WHERE rowid = ?", (rid,)
    ).fetchone()
    assert doc[1] == "GN164"
