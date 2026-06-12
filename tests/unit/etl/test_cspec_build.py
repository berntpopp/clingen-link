import sqlite3

from clingen_link.etl import build, cspec_parse, schema


def _spec_inputs():
    jsonld = {
        "@id": ".../id/GN164",
        "affiliation": {"@id": ".../id/50140", "label": "ABCA4 VCEP"},
        "label": "ABCA4 spec v1",
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
    html = '<h3>BS3</h3><a href="/cspec/File/id/abc/data">x</a>'
    heads = {
        "https://cspec.genome.network/cspec/File/id/abc/data": {
            "content-disposition": "filename=ABCA4-BS3.xlsx",
            "content-type": "x",
            "content-length": "5",
        }
    }
    return cspec_parse.parse_spec(jsonld, html, heads)


def _multi_entity_spec():
    """One spec with two distinct criteria + two distinct files.

    PM2 -> criteria_id 111 -> file abc-1; PVS1 -> criteria_id 222 -> file def-2.
    The file regex requires hex-style ids (``abc-1`` / ``def-2``) and attribution
    walks the doc HTML in order, so each file lands under its own heading. This is
    the fixture that exposes a shared-rowid-counter drift in ``_write_cspec``.
    """
    jsonld = {
        "@id": ".../id/GN200",
        "affiliation": {"@id": ".../id/50200", "label": "Multi VCEP"},
        "label": "Multi spec v1",
        "version": "2.0.0",
        "cspecStatus": "Released",
        "ruleSets": [
            {
                "@id": ".../id/888",
                "genes": [
                    {
                        "@id": ".../?query=GENEX",
                        "diseases": [{"label": "MONDO:0000001"}],
                        "modeOfInheritance": "AD",
                    }
                ],
                "criteriaCodes": [
                    {
                        "@id": ".../id/111",
                        "label": "PM2",
                        "description": "absent from controls",
                        "evidenceStrengths": [{"label": "Moderate", "applicability": "Applicable"}],
                    },
                    {
                        "@id": ".../id/222",
                        "label": "PVS1",
                        "description": "null variant",
                        "evidenceStrengths": [
                            {"label": "VeryStrong", "applicability": "Applicable"}
                        ],
                    },
                ],
            }
        ],
    }
    html = (
        '<h3>PM2</h3><a href="/cspec/File/id/abc-1/data">x</a>'
        '<h3>PVS1</h3><a href="/cspec/File/id/def-2/data">y</a>'
    )
    heads = {
        "https://cspec.genome.network/cspec/File/id/abc-1/data": {
            "content-disposition": "filename=GENEX-PM2.xlsx",
            "content-type": "x",
            "content-length": "5",
        },
        "https://cspec.genome.network/cspec/File/id/def-2/data": {
            "content-disposition": "filename=GENEX-PVS1.xlsx",
            "content-type": "y",
            "content-length": "7",
        },
    }
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


def test_write_cspec_rowid_map_lockstep_multi_entity() -> None:
    """A shared-rowid-counter drift would make FTS hits resolve to a neighbor.

    Asserts each criterion-code MATCH resolves to that criterion's own
    ``criteria_id`` and each filename MATCH resolves to that file's own
    ``file_uuid``. If the rowid counter ever drifted between ``cspec_fts`` and
    ``cspec_search_doc``, a PM2 match would resolve to PVS1 (or to a file) and
    this test would fail.
    """
    parsed = _multi_entity_spec()
    # Guard: the fixture must actually yield 2 criteria + 2 files for the test to
    # be meaningful.
    assert len(parsed.criteria) == 2
    assert len(parsed.files) == 2

    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    assert build._write_cspec(conn, [parsed]) == 1

    def resolve(query: str):
        rid = conn.execute(
            "SELECT rowid FROM cspec_fts WHERE cspec_fts MATCH ?", (query,)
        ).fetchone()[0]
        return conn.execute(
            "SELECT entity_type, criteria_id, file_uuid FROM cspec_search_doc WHERE rowid = ?",
            (rid,),
        ).fetchone()

    # Each code match must resolve to its OWN criterion, not the neighbor's.
    pm2 = resolve('"PM2"')
    assert pm2[0] == "criterion"
    assert pm2[1] == "111"
    pvs1 = resolve('"PVS1"')
    assert pvs1[0] == "criterion"
    assert pvs1[1] == "222"

    # Each filename match must resolve to its OWN file, not the neighbor's.
    f_pm2 = resolve('"GENEX-PM2.xlsx"')
    assert f_pm2[0] == "file"
    assert f_pm2[2] == "abc-1"
    f_pvs1 = resolve('"GENEX-PVS1.xlsx"')
    assert f_pvs1[0] == "file"
    assert f_pvs1[2] == "def-2"
