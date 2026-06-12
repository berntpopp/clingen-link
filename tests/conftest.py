"""Pytest configuration and shared fixtures for clingen-link."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastmcp import FastMCP

from clingen_link.api.clingen_client import ClingenClient
from clingen_link.etl import build
from clingen_link.mcp.facade import create_clingen_mcp
from clingen_link.mcp.service_adapters import reset_services, set_services
from clingen_link.services.aggregator import ClingenServices
from clingen_link.store.db import Store

_FIXTURES = Path(__file__).parent / "fixtures"
# Deterministic build timestamp so the small snapshot's meta rows are stable.
_TEST_FETCHED_AT = "2026-06-12T00:00:00.000Z"


def _load_test_sources() -> build.Sources:
    """Assemble ETL Sources from the small fixtures for a tiny test snapshot."""
    validity = json.loads((_FIXTURES / "validity_api_small.json").read_text())
    brief = json.loads((_FIXTURES / "actionability_brief_small.json").read_text())
    erepo_summary = json.loads((_FIXTURES / "erepo_summary_sample.json").read_text())
    news = json.loads((_FIXTURES / "erepo_news_sample.json").read_text())
    affiliates = json.loads((_FIXTURES / "affiliates_sample.json").read_text())
    return build.Sources(
        validity_rows=validity["rows"],
        dosage_gene_tsv=(_FIXTURES / "dosage_gene_GRCh38.head.tsv").read_text(),
        dosage_region_tsv=(_FIXTURES / "dosage_region_GRCh38.head.tsv").read_text(),
        dosage_etags={"gene_grch38": '"abc"', "region_grch38": '"def"'},
        actionability_brief=brief,
        erepo_tsv=(_FIXTURES / "erepo_bulk.head.tsv").read_text(),
        erepo_news=news["data"],
        erepo_summary=erepo_summary,
        affiliates=affiliates["rows"],
    )


@pytest.fixture(scope="session")
def test_snapshot_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a small SQLite snapshot from fixtures once per session.

    The store tests run against this tiny DB (a handful of rows per domain), not
    the 54 MB shipped snapshot, so they stay fast and hermetic.
    """
    out = tmp_path_factory.mktemp("snapshot") / "clingen-test.sqlite"
    build.build_snapshot(out, _load_test_sources(), _TEST_FETCHED_AT)
    return out


@pytest.fixture
def store(test_snapshot_path: Path) -> Iterator[Store]:
    """A read-only :class:`Store` over the small test snapshot."""
    s = Store(test_snapshot_path)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _reset_service_singleton() -> Iterator[None]:
    """Reset the service_adapters singleton around every test.

    Prevents a previous case's injected services / cached default from leaking
    into the next one.
    """
    reset_services()
    yield
    reset_services()


@pytest.fixture
def mcp() -> FastMCP:
    """A clingen-link MCP server built from the facade with default services."""
    return create_clingen_mcp()


# Base URLs the injected live client points at, so respx mocks can target them.
EREPO_TEST_BASE = "https://erepo.test/evrepo"
ACTION_TEST_BASE = "https://actionability.test/ac"


@pytest.fixture
async def tool_services(store: Store) -> AsyncIterator[ClingenServices]:
    """A ClingenServices over the small test store + a test-pointed live client.

    Yields the container so tests can assert against it directly; closing the
    live client is handled here.
    """
    client = ClingenClient(
        erepo_base=EREPO_TEST_BASE,
        actionability_base=ACTION_TEST_BASE,
        timeout_s=1.0,
        queue_wait_timeout_s=1.0,
    )
    svc = ClingenServices(store, client=client)
    try:
        yield svc
    finally:
        await svc.client.aclose()


@pytest.fixture
def tool_mcp(tool_services: ClingenServices) -> FastMCP:
    """An MCP server wired to the small-snapshot services (full tool surface)."""
    set_services(tool_services)
    return create_clingen_mcp()
