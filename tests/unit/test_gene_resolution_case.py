"""Issue #159: a string that IS an approved symbol (in any case) resolves to that symbol.

The resolver used to try an exact alias before a case-insensitive symbol, and the alias table
also stores casefolded aliases, so ``ar`` hit FDXR's alias ``AR`` before the symbol AR
(``agt`` -> AGXT, ``dsc2`` -> DSC3, ``c8orf37`` -> CFAP418 in the 2026-11 snapshot). An alias
shared by several genes was resolved to the alphabetically first one, also silently.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from clingen_link.exceptions import AmbiguousQueryError
from clingen_link.store.db import Store


def _index(genes: list[str], aliases: list[tuple[str, str]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE gene (symbol TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE gene_alias (alias TEXT, symbol TEXT, PRIMARY KEY (alias, symbol))")
    conn.executemany("INSERT INTO gene VALUES (?)", [(g,) for g in genes])
    # The ETL stores every alias as given AND casefolded.
    rows = {(a, s) for a, s in aliases} | {(a.casefold(), s) for a, s in aliases}
    conn.executemany("INSERT INTO gene_alias VALUES (?, ?)", sorted(rows))
    return conn


@pytest.mark.parametrize(
    ("query", "expected"),
    [("ar", "AR"), ("Ar", "AR"), ("agt", "AGT"), ("dsc2", "DSC2"), ("c8orf37", "C8orf37")],
)
def test_symbol_in_any_case_beats_another_genes_alias(query: str, expected: str) -> None:
    conn = _index(
        ["AR", "FDXR", "AGT", "AGXT", "DSC2", "DSC3", "C8orf37", "CFAP418"],
        [("AR", "FDXR"), ("AGT", "AGXT"), ("DSC2", "DSC3"), ("C8ORF37", "CFAP418")],
    )
    assert Store._resolve_with(conn, query) == expected


def test_a_unique_alias_still_resolves() -> None:
    conn = _index(["EGFR"], [("ERBB1", "EGFR")])
    assert Store._resolve_with(conn, "erbb1") == "EGFR"


def test_an_alias_shared_by_several_genes_is_ambiguous_not_a_silent_pick() -> None:
    conn = _index(["ACVRL1", "ALK"], [("ALK1", "ACVRL1"), ("ALK1", "ALK")])
    with pytest.raises(AmbiguousQueryError, match="ACVRL1, ALK"):
        Store._resolve_with(conn, "alk1")


def _snapshots() -> list[str]:
    """The test snapshot always; a real bundle too when CLINGEN_LINK_SNAPSHOT_PATH names one."""
    real = os.environ.get("CLINGEN_LINK_SNAPSHOT_PATH", "")
    return ["test", *([real] if real and Path(real).is_file() else [])]


@pytest.mark.parametrize("which", _snapshots())
def test_every_approved_symbol_resolves_to_itself_in_lower_and_upper_case(
    which: str, test_snapshot_path: Path
) -> None:
    store = Store(test_snapshot_path if which == "test" else Path(which))
    try:
        with store.connection() as conn:
            symbols = [r["symbol"] for r in conn.execute("SELECT symbol FROM gene")]
            assert symbols
            wrong = {
                form: got
                for symbol in symbols
                for form in (symbol.lower(), symbol.upper())
                if (got := Store._resolve_with(conn, form)) != symbol
            }
    finally:
        store.close()
    assert wrong == {}


@pytest.mark.asyncio
async def test_search_genes_lists_candidates_for_an_ambiguous_alias(
    tool_mcp, tool_services, monkeypatch: pytest.MonkeyPatch
) -> None:
    def ambiguous(_query: str) -> str:
        raise AmbiguousQueryError("several")

    monkeypatch.setattr(tool_services.store, "resolve_gene", ambiguous)
    result = await tool_mcp.call_tool("search_genes", {"query": "AARS"})
    payload = result.structured_content or {}
    assert payload["success"] is True
    assert payload["resolved_symbol"] is None
    assert {c["symbol"] for c in payload["results"]} >= {"AARS1", "AARS2"}
