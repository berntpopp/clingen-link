"""Tests for the smaller MCP infra modules: next_commands, patterns, resources, adapters."""

from __future__ import annotations

import re

from clingen_link.mcp import next_commands as nc
from clingen_link.mcp import patterns
from clingen_link.mcp.resources import (
    MCP_PROTOCOL_VERSION,
    RESEARCH_USE_NOTICE,
    _server_version,
    get_capabilities_resource,
    get_research_use_resource,
)
from clingen_link.mcp.service_adapters import (
    ClingenServices,
    get_services,
    reset_services,
    set_services,
)


def test_cmd_shape() -> None:
    entry = nc.cmd("get_gene_summary", gene="BRCA1")
    assert entry == {"tool": "get_gene_summary", "arguments": {"gene": "BRCA1"}}


def test_builders_emit_callable_entries() -> None:
    for builder, arg in (
        (nc.for_gene, "BRCA1"),
        (nc.for_disease, "MONDO:0007254"),
        (nc.for_variant, "CA123456"),
    ):
        entries = builder(arg)
        assert entries, builder
        for entry in entries:
            assert entry["tool"]
            assert entry["arguments"]  # never empty


def test_patterns_match_expected() -> None:
    assert re.match(patterns.HGNC_ID_PATTERN, "HGNC:1100")
    assert re.match(patterns.MONDO_PATTERN, "MONDO:0007254")
    assert re.match(patterns.CAID_PATTERN, "CA123456")
    assert re.match(patterns.CAID_PATTERN, "CAR:123456")
    assert re.match(patterns.DOC_ID_PATTERN, "AC161")
    assert re.match(patterns.GENE_SYMBOL_PATTERN, "BRCA1")
    assert re.match(patterns.HGVS_PATTERN, "NM_000059.4:c.68_69del")


def test_patterns_reject_bad_input() -> None:
    assert not re.match(patterns.HGNC_ID_PATTERN, "1100")
    assert not re.match(patterns.MONDO_PATTERN, "MONDO007")
    assert not re.match(patterns.DOC_ID_PATTERN, "161")


def test_capabilities_resource_shape() -> None:
    cap = get_capabilities_resource()
    assert cap["server"] == "clingen-link"
    assert cap["mcp_protocol_version"] == MCP_PROTOCOL_VERSION
    assert "validity" in cap["datasets"]
    assert "snapshot_unavailable" in cap["error_codes"]
    assert cap["research_use_notice"] == RESEARCH_USE_NOTICE


def test_research_use_resource() -> None:
    assert get_research_use_resource() == {"notice": RESEARCH_USE_NOTICE}


def test_server_version_is_str() -> None:
    assert isinstance(_server_version(), str)


def test_service_adapters_inject_and_reset(test_snapshot_path: object) -> None:
    from clingen_link.services.aggregator import ClingenServices as RealServices

    reset_services()
    sentinel = RealServices.from_snapshot(test_snapshot_path)  # type: ignore[arg-type]
    try:
        set_services(sentinel)
        assert get_services() is sentinel
        assert isinstance(get_services(), ClingenServices)
        reset_services()
        # After reset the override is cleared; a new build would load the bundled
        # snapshot, so we only assert the override is gone (not the default build).
        from clingen_link.mcp import service_adapters

        assert service_adapters._override is None
    finally:
        import asyncio

        asyncio.run(sentinel.aclose())
        reset_services()
