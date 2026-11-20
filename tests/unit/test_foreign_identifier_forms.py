"""A well-formed identifier from another namespace is never answered as an absence.

Response-Envelope Standard v1 §2b (genefoundry-router#343). The snapshot carries no Ensembl /
NCBI Gene / RefSeq cross-reference, so those forms are ``invalid_input`` naming the field, the
accepted forms and a ready-to-send converter call — never "has no ClinGen curation", which
callers read as "this gene is uncurated" when the input was simply not understood.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from clingen_link.mcp.foreign_ids import foreign_gene

pytestmark = pytest.mark.asyncio

# tool -> its gene parameter (every gene-keyed tool; same set as test_gene_miss_no_loop).
GENE_KEYED = {
    "search_genes": "query",
    "get_gene_summary": "gene_symbol",
    "get_gene_validity": "gene_symbol",
    "get_gene_dosage": "gene_symbol",
    "get_gene_actionability": "gene_symbol",
    "search_validity": "gene_symbol",
    "search_actionability": "gene_symbol",
    "get_variant_interpretations": "gene_symbol",
    "list_cspecs": "gene_symbol",
}
HGNC = {"tool": "hgnc_resolve_symbol", "arguments": {"query": "ENSG00000133703"}}
XREF = {"tool": "hgnc_resolve_gene_by_xref", "arguments": {"source": "entrez_id", "value": "3845"}}
FOREIGN = [
    (" ENSG00000133703 ", HGNC),  # padded: stripped once, still names the converter
    ("entrez:0003845", XREF),  # leading zeros dropped; the xref tool misses a padded id
    ("ENSG00000133703", HGNC),
    ("3845", XREF),
    ("NCBIGene:3845", XREF),  # rejected by the schema pattern where one is declared
    ("NM_004985.5", {"tool": "hgnc_resolve_symbol", "arguments": {"query": "NM_004985.5"}}),
]


async def _call(mcp: FastMCP, tool: str, args: dict[str, object]) -> tuple[dict, bool]:
    result = await mcp.call_tool(tool, args)
    return dict(result.structured_content or {}), bool(result.is_error)


@pytest.mark.parametrize("tool", GENE_KEYED)
@pytest.mark.parametrize(("value", "converter"), FOREIGN)
async def test_foreign_gene_form_names_the_converter(
    tool_mcp: FastMCP, tool: str, value: str, converter: dict[str, object]
) -> None:
    param = GENE_KEYED[tool]
    env, is_error = await _call(tool_mcp, tool, {param: value})
    assert is_error and env["success"] is False
    assert env["error_code"] == "invalid_input"
    assert env["field"] == param
    assert any("HGNC" in form for form in env["allowed_values"])
    assert env["hint"]
    assert env["_meta"]["next_commands"][0] == converter
    assert all(c["tool"] != tool for c in env["_meta"]["next_commands"])  # no self-loop
    text = env["message"] + env["recovery"]
    assert "no ClinGen curation" not in text and "honest absence" not in text


@pytest.mark.parametrize("tool", GENE_KEYED)
@pytest.mark.parametrize("value", ["ENST00000311936", "ENSP00000256078"])
async def test_transcript_id_gets_no_converter_it_cannot_guarantee(
    tool_mcp: FastMCP, tool: str, value: str
) -> None:
    env, is_error = await _call(tool_mcp, tool, {GENE_KEYED[tool]: value})
    assert is_error and env["error_code"] == "invalid_input"
    assert env["field"] == GENE_KEYED[tool]
    assert "transcript or protein id" in env["message"] and "takes a gene" in env["message"]
    assert env["fallback_tool"] in {c["tool"] for c in env["_meta"]["next_commands"]}
    assert "no ClinGen curation" not in env["message"]
    called = {c["tool"] for c in env["_meta"]["next_commands"]}
    assert not called & {"hgnc_resolve_symbol", "hgnc_resolve_gene_by_xref", tool}


async def test_bare_number_states_how_it_was_read(tool_mcp: FastMCP) -> None:
    env, _ = await _call(tool_mcp, "get_gene_validity", {"gene_symbol": "3845"})
    assert "NCBI Gene id" in env["message"]
    assert "HGNC:3845" in env["hint"]  # the alternative reading, offered, not assumed


@pytest.mark.parametrize("tool", GENE_KEYED)
async def test_real_symbol_without_curation_stays_an_honest_absence(
    tool_mcp: FastMCP, tool: str
) -> None:
    env, is_error = await _call(tool_mcp, tool, {GENE_KEYED[tool]: "ZZZNOPE"})
    assert is_error and env["error_code"] == "not_found"
    assert "no ClinGen curation" in env["message"]
    assert "field" not in env


@pytest.mark.parametrize("value", ["HGNC:999999999", "P2RY10", "B3GAT1", "C1orf112"])
async def test_understood_forms_are_not_mistaken_for_foreign_ones(value: str) -> None:
    assert foreign_gene(value, "gene_symbol") is None


@pytest.mark.parametrize(
    ("tool", "param"),
    [
        ("search_validity", "mondo"),
        ("search_validity", "disease"),
        ("search_actionability", "disease"),
        ("get_variant_interpretations", "disease"),
    ],
)
async def test_foreign_disease_id_names_mondo(tool_mcp: FastMCP, tool: str, param: str) -> None:
    env, is_error = await _call(tool_mcp, tool, {param: "OMIM:163950"})
    assert is_error and env["error_code"] == "invalid_input"
    assert env["field"] == param
    assert any("MONDO:" in form for form in env["allowed_values"])
    assert env["_meta"]["next_commands"][0] == {
        "tool": "mondo_resolve_disease",
        "arguments": {"query": "OMIM:163950"},
    }


async def test_pattern_mismatch_states_the_accepted_format(tool_mcp: FastMCP) -> None:
    env, _ = await _call(tool_mcp, "search_validity", {"mondo": "Noonan syndrome"})
    reason = env["field_errors"][0]["reason"]
    assert "MONDO:0007254" in reason and "mondo_resolve_disease" in reason
    # Free text is not a recognised id form, so it is never echoed into a command.
    assert env["_meta"]["next_commands"] == [{"tool": "get_diagnostics", "arguments": {}}]


# Arabic-indic digits, fullwidth digits, a long s (casefolds to "s"), mixed: written as
# escapes so the source stays unambiguous.
@pytest.mark.parametrize(
    "value",
    [
        "\u0661\u0662\u0663",
        "\uff11\uff12\uff13",
        "EN\u017fG00000133703",
        "ENSG\u0660\u0660\u0660\u0660\u0660\u0661\u0663\u0663\u0667\u0660\u0663",
    ],
)
async def test_non_ascii_lookalikes_are_never_echoed(tool_mcp: FastMCP, value: str) -> None:
    assert foreign_gene(value, "gene_symbol") is None
    env, _ = await _call(tool_mcp, "search_validity", {"gene_symbol": value})  # no schema pattern
    assert value not in str(env)


@pytest.mark.parametrize(("query", "converter"), [("ENSG00000133703", HGNC), ("3845", XREF)])
async def test_search_dosage_does_not_answer_a_foreign_gene_with_zero_rows(
    tool_mcp: FastMCP, query: str, converter: dict[str, object]
) -> None:
    env, is_error = await _call(tool_mcp, "search_dosage", {"query": query})
    assert is_error and env["error_code"] == "invalid_input" and env["field"] == "query"
    assert env["_meta"]["next_commands"][0] == converter


async def test_padded_foreign_disease_id_still_names_mondo(tool_mcp: FastMCP) -> None:
    env, _ = await _call(tool_mcp, "search_validity", {"disease": "OMIM:163950 "})
    assert env["error_code"] == "invalid_input"
    assert env["_meta"]["next_commands"][0]["arguments"] == {"query": "OMIM:163950"}


@pytest.mark.parametrize("tool", GENE_KEYED)
async def test_lower_case_hgnc_prefix_is_accepted(tool_mcp: FastMCP, tool: str) -> None:
    upper, _ = await _call(tool_mcp, tool, {GENE_KEYED[tool]: "HGNC:20"})
    lower, is_error = await _call(tool_mcp, tool, {GENE_KEYED[tool]: "hgnc:20"})
    assert upper["success"] is True and not is_error and lower["success"] is True
