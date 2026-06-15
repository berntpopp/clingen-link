"""Tests for the ERepo variant tools (Task 4.6)."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastmcp import FastMCP

from tests.conftest import EREPO_TEST_BASE

pytestmark = pytest.mark.asyncio


async def _call(mcp: FastMCP, name: str, args: dict[str, object]) -> dict[str, object]:
    result = await mcp.call_tool(name, args)
    return result.structured_content or {}


class TestGetVariantInterpretations:
    async def test_by_gene(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_variant_interpretations", {"gene_symbol": "BRAF"})
        assert payload["success"] is True
        assert payload["total"] == 1
        rec = payload["records"][0]
        assert rec["caid"] == "CA281951"
        assert rec["assertion"] == "Likely Pathogenic"
        assert rec["recommended_citation"].startswith("ClinGen Variant Pathogenicity")
        # next_command drills into the single interpretation.
        nxt = payload["_meta"]["next_commands"][0]
        assert nxt["tool"] == "get_variant_interpretation"
        assert nxt["arguments"] == {"caid": "CA281951"}

    async def test_classification_filter(self, tool_mcp: FastMCP) -> None:
        payload = await _call(
            tool_mcp,
            "get_variant_interpretations",
            {"gene_symbol": "PAH", "classification": "Pathogenic"},
        )
        assert payload["success"] is True
        assert all(r["assertion"] == "Pathogenic" for r in payload["records"])

    async def test_pagination_truncated(self, tool_mcp: FastMCP) -> None:
        payload = await _call(
            tool_mcp, "get_variant_interpretations", {"gene_symbol": "PAH", "size": 1}
        )
        assert payload["total"] >= 2
        trunc = payload["_meta"]["truncated"]
        assert trunc["kind"] == "pagination"
        assert trunc["dropped"] >= 1

    async def test_truncation_echoes_expert_panel(self, tool_mcp: FastMCP) -> None:
        # L1: PAH has 3 variants under one VCEP; size=1 forces truncation that must echo the filter.
        payload = await _call(
            tool_mcp,
            "get_variant_interpretations",
            {"expert_panel": "Phenylketonuria", "size": 1},
        )
        trunc = payload["_meta"]["truncated"]
        assert trunc["dropped"] >= 1
        assert trunc["filter"]["expert_panel"] == "Phenylketonuria"

    async def test_empty_result_has_next(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_variant_interpretations", {"gene_symbol": "ZZZNOPE"})
        assert payload["success"] is True
        assert payload["total"] == 0
        for c in payload["_meta"]["next_commands"]:
            assert c["tool"] and c["arguments"]


class TestGetVariantInterpretation:
    async def test_by_caid_snapshot(self, tool_mcp: FastMCP) -> None:
        payload = await _call(tool_mcp, "get_variant_interpretation", {"caid": "CA281951"})
        assert payload["success"] is True
        assert payload["source"] == "snapshot"
        assert payload["interpretation"]["gene"] == "BRAF"
        assert payload["recommended_citation"].startswith("ClinGen Variant Pathogenicity")

    async def test_by_hgvs_snapshot(self, tool_mcp: FastMCP) -> None:
        payload = await _call(
            tool_mcp, "get_variant_interpretation", {"hgvs": "NM_004333.4:c.740T>C"}
        )
        assert payload["success"] is True
        assert payload["interpretation"]["caid"] == "CA281951"

    async def test_by_clinvar_id_snapshot(self, tool_mcp: FastMCP) -> None:
        payload = await _call(
            tool_mcp, "get_variant_interpretation", {"clinvar_variation_id": "17000"}
        )
        assert payload["success"] is True
        assert payload["interpretation"]["gene"] == "GJB2"

    async def test_evidence_codes_in_full(self, tool_mcp: FastMCP) -> None:
        payload = await _call(
            tool_mcp,
            "get_variant_interpretation",
            {"caid": "CA281951", "response_mode": "full"},
        )
        interp = payload["interpretation"]
        assert "evidence_codes_met" in interp

    async def test_multiple_selectors_invalid(self, tool_mcp: FastMCP) -> None:
        payload = await _call(
            tool_mcp,
            "get_variant_interpretation",
            {"caid": "CA281951", "hgvs": "NM_004333.4:c.740T>C"},
        )
        assert payload["success"] is False
        assert payload["error_code"] == "invalid_input"

    @respx.mock
    async def test_refresh_forces_live(self, tool_mcp: FastMCP) -> None:
        respx.get(f"{EREPO_TEST_BASE}/api/summary/news/").mock(
            return_value=httpx.Response(200, json={"data": [{"relatedVersion": "2.5.6"}]})
        )
        route = respx.get(f"{EREPO_TEST_BASE}/api/classifications").mock(
            return_value=httpx.Response(
                200, json=[{"caid": "CA281951", "gene": "BRAF", "assertion": "Pathogenic"}]
            )
        )
        payload = await _call(
            tool_mcp, "get_variant_interpretation", {"caid": "CA281951", "refresh": True}
        )
        assert payload["success"] is True
        assert payload["source"] == "live"
        assert payload["interpretation"]["assertion"] == "Pathogenic"
        assert route.called

    @respx.mock
    async def test_refresh_dict_gene_shape_no_validation_failed(self, tool_mcp: FastMCP) -> None:
        # H1: the real ERepo summary returns gene as a dict; this must NOT become validation_failed.
        respx.get(f"{EREPO_TEST_BASE}/api/summary/news/").mock(
            return_value=httpx.Response(200, json={"data": [{"relatedVersion": "2.5.6"}]})
        )
        respx.get(f"{EREPO_TEST_BASE}/api/classifications").mock(
            return_value=httpx.Response(
                200,
                json={
                    "variantInterpretations": [
                        {
                            "caid": "CA281951",
                            "gene": {"label": "BRAF", "NCBI_id": "673"},
                            "assertion": "Pathogenic",
                            "hgvs": ["NC_000007.14:g.140753336A>T"],
                        }
                    ]
                },
            )
        )
        payload = await _call(
            tool_mcp, "get_variant_interpretation", {"caid": "CA281951", "refresh": True}
        )
        assert payload["success"] is True
        assert payload.get("error_code") != "validation_failed"
        assert payload["source"] == "live"
        assert payload["interpretation"]["gene"] == "BRAF"

    @respx.mock
    async def test_refresh_degrades_to_snapshot_not_validation_failed(
        self, tool_mcp: FastMCP
    ) -> None:
        # H1: a live upstream fault degrades to the snapshot — never a bad-input error envelope.
        respx.get(f"{EREPO_TEST_BASE}/api/summary/news/").mock(
            return_value=httpx.Response(200, json={"data": [{"relatedVersion": "2.5.6"}]})
        )
        respx.get(f"{EREPO_TEST_BASE}/api/classifications").mock(return_value=httpx.Response(400))
        payload = await _call(
            tool_mcp, "get_variant_interpretation", {"caid": "CA281951", "refresh": True}
        )
        assert payload["success"] is True
        assert payload.get("error_code") != "validation_failed"
        assert payload["source"] == "snapshot"
        assert payload["notice"]
        assert payload["interpretation"]["gene"] == "BRAF"
