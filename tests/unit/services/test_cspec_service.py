import sqlite3

import pytest

from clingen_link.etl import build, cspec_parse, schema
from clingen_link.services.cspec_service import CspecService
from clingen_link.store.db import Store


@pytest.fixture
def store(tmp_path) -> Store:
    db = tmp_path / "snap.sqlite"
    conn = sqlite3.connect(db)
    schema.create_schema(conn)
    jsonld = {
        "@id": ".../id/GN092",
        "affiliation": {"@id": ".../id/50087", "label": "ENIGMA"},
        "label": "ENIGMA spec",
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
                        "description": "null",
                        "evidenceStrengths": [],
                    }
                ],
            }
        ],
    }
    build._write_cspec(conn, [cspec_parse.parse_spec(jsonld, "", {})])
    conn.commit()
    conn.close()
    return Store(db)


@pytest.fixture
def populated_store(tmp_path) -> Store:
    """A spec whose single criterion has a strength and an attached file."""
    db = tmp_path / "populated.sqlite"
    conn = sqlite3.connect(db)
    schema.create_schema(conn)
    jsonld = {
        "@id": ".../id/GN200",
        "affiliation": {"@id": ".../id/50099", "label": "TEST VCEP"},
        "label": "TEST spec",
        "version": "1.0.0",
        "cspecStatus": "Released",
        "ruleSets": [
            {
                "@id": ".../id/1",
                "genes": [
                    {
                        "@id": ".../?query=TP53",
                        "diseases": [{"label": "MONDO:0018875"}],
                        "modeOfInheritance": "AD",
                    }
                ],
                "criteriaCodes": [
                    {
                        "@id": ".../id/777",
                        "label": "PS3",
                        "description": "functional assay",
                        "evidenceStrengths": [
                            {
                                "label": "Supporting",
                                "applicability": "Applicable",
                                "description": "see file",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    html = '<h3>PS3</h3><a href="/cspec/File/id/abc-1/data">x</a>'
    heads = {
        "https://cspec.genome.network/cspec/File/id/abc-1/data": {
            "content-disposition": "filename=PS3.xlsx",
            "content-type": "x",
            "content-length": "5",
        }
    }
    build._write_cspec(conn, [cspec_parse.parse_spec(jsonld, html, heads)])
    conn.commit()
    conn.close()
    return Store(db)


@pytest.mark.asyncio
async def test_get_detail_and_resolve(store) -> None:
    svc = CspecService(store)
    detail = await svc.get_detail(gn_id="GN092")
    assert detail is not None and detail.criteria[0].code == "PVS1"
    assert await svc.resolve_for_erepo(affiliation_id="50087", gene="BRCA1") == ["GN092"]


@pytest.mark.asyncio
async def test_search(store) -> None:
    svc = CspecService(store)
    _hits, total = await svc.search(text="ENIGMA")
    assert total >= 1


@pytest.mark.asyncio
async def test_detail_has_populated_strengths_and_files(populated_store) -> None:
    svc = CspecService(populated_store)
    detail = await svc.get_detail(gn_id="GN200")
    assert detail is not None
    criterion = detail.criteria[0]
    assert criterion.code == "PS3"
    assert criterion.strengths[0].strength_label == "Supporting"
    # The PS3 heading is unambiguous, so the file associates at criterion level.
    assert criterion.files[0].filename == "PS3.xlsx"


@pytest.mark.asyncio
async def test_get_criterion(populated_store) -> None:
    svc = CspecService(populated_store)
    criterion = await svc.get_criterion(criteria_id="777")
    assert criterion is not None and criterion.code == "PS3"
    assert await svc.get_criterion(criteria_id="does-not-exist") is None


@pytest.mark.asyncio
async def test_resolve_criterion_ids(populated_store) -> None:
    svc = CspecService(populated_store)
    assert await svc.resolve_criterion_ids(gn_id="GN200", code="PS3") == ["777"]
    assert await svc.resolve_criterion_ids(gn_id="GN200", code="NOPE") == []
