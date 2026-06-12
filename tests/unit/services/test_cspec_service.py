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
