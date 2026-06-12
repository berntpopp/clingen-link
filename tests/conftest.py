"""Pytest configuration and shared fixtures for clingen-link."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastmcp import FastMCP

from clingen_link.mcp.facade import create_clingen_mcp
from clingen_link.mcp.service_adapters import reset_services


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
