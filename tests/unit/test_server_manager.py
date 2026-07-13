"""Tests for clingen_link.server_manager.UnifiedServerManager."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastmcp import FastMCP

from clingen_link.config import ServerConfig
from clingen_link.exceptions import ConfigurationError
from clingen_link.mcp.service_adapters import ClingenServices, set_services
from clingen_link.server_manager import UnifiedServerManager


def _manager() -> UnifiedServerManager:
    manager = UnifiedServerManager()
    manager.logger = logging.getLogger("test.clingen")
    return manager


@pytest.fixture
def injected_services(test_snapshot_path: Path) -> Iterator[ClingenServices]:
    """Inject a real services container built from the small test snapshot."""
    services = ClingenServices.from_snapshot(test_snapshot_path)
    set_services(services)
    try:
        yield services
    finally:
        services.store.close()


def test_create_services_returns_container(injected_services: ClingenServices) -> None:
    assert _manager()._create_services() is injected_services


def test_create_mcp_server_builds_facade(injected_services: ClingenServices) -> None:
    manager = _manager()
    mcp = manager._create_mcp_server(lambda: injected_services)
    assert isinstance(mcp, FastMCP)


async def test_fastapi_app_health_endpoint(injected_services: ClingenServices) -> None:
    manager = _manager()
    manager._current_transport = "http"
    config = ServerConfig(transport="http")
    app = await manager._create_fastapi_app(config)

    routes = {getattr(r, "path", None) for r in app.routes}
    assert "/health" in routes

    # Locate and invoke the health handler directly. A READY host (its reference
    # snapshot selected) answers with the plain healthy body; a not-ready host is
    # covered by test_health_is_not_ready_without_a_snapshot.
    health_route = next(r for r in app.routes if getattr(r, "path", None) == "/health")
    async with app.router.lifespan_context(app):
        result = await health_route.endpoint()
    assert result["status"] == "healthy"
    assert result["transport"] == "streamable-http-stateless"


async def test_health_endpoint_has_version_and_transport(
    injected_services: ClingenServices,
) -> None:
    """Health MUST carry {status, version, transport} per MCP Transport Standard v1."""
    from clingen_link import __version__

    manager = _manager()
    config = ServerConfig(transport="unified")
    app = await manager._create_fastapi_app(config)

    health_route = next(r for r in app.routes if getattr(r, "path", None) == "/health")
    async with app.router.lifespan_context(app):
        result = await health_route.endpoint()

    assert "status" in result, "health missing 'status'"
    assert "version" in result, "health missing 'version' (MCP Transport Standard v1)"
    assert "transport" in result, "health missing 'transport' (MCP Transport Standard v1)"
    assert result["version"] == __version__
    assert result["transport"] == "streamable-http-stateless"


async def test_start_server_rejects_unknown_transport() -> None:
    manager = _manager()
    config = ServerConfig(transport="unified")
    object.__setattr__(config, "transport", "bogus")
    with pytest.raises(ConfigurationError):
        await manager.start_server(config)


async def test_compose_lifespan_runs_both_contexts(
    injected_services: ClingenServices,
) -> None:
    manager = _manager()
    manager._current_transport = "unified"
    app = await manager._create_fastapi_app(ServerConfig(transport="unified"))

    mcp = manager._create_mcp_server(lambda: injected_services)
    mcp_http_app = mcp.http_app(path="/", stateless_http=True, json_response=True)
    manager._compose_lifespan(app, mcp_http_app)

    # The composed lifespan should enter and exit cleanly (services initialized).
    async with app.router.lifespan_context(app):
        assert app.state.clingen_services is not None


def test_module_create_app_factory() -> None:
    from clingen_link.server_manager import create_app

    app = create_app()
    routes = {getattr(r, "path", None) for r in app.routes}
    assert "/health" in routes


# --- code-only image: live before the reference snapshot is materialized -------------------
#
# The production image ships no ClinGen database. The fleet release gate runs that exact
# image standalone (`docker run`, no init sidecar, no reference volume) and requires MCP
# `initialize` + `tools/list` to answer. The host must therefore start without a snapshot
# and fail closed only at the data boundary.


async def test_host_starts_without_a_materialized_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clingen_link.config import settings
    from clingen_link.mcp.service_adapters import reset_services

    reset_services()
    monkeypatch.setattr(settings, "snapshot_path", str(tmp_path / "absent.sqlite"))

    manager = _manager()
    app = await manager._create_fastapi_app(ServerConfig(transport="unified"))

    async with app.router.lifespan_context(app):
        assert app.state.clingen_services is None
        assert app.state.clingen_data_error


async def test_health_is_not_ready_without_a_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from clingen_link.config import settings
    from clingen_link.mcp.service_adapters import reset_services

    reset_services()
    monkeypatch.setattr(settings, "snapshot_path", str(tmp_path / "absent.sqlite"))

    manager = _manager()
    app = await manager._create_fastapi_app(ServerConfig(transport="unified"))
    health_route = next(r for r in app.routes if getattr(r, "path", None) == "/health")

    async with app.router.lifespan_context(app):
        response = await health_route.endpoint()

    assert response.status_code == 503
    body = json.loads(bytes(response.body))
    assert body["status"] == "degraded"
    assert body["data_available"] is False


async def test_health_reports_data_identity_when_ready(
    injected_services: ClingenServices,
) -> None:
    manager = _manager()
    app = await manager._create_fastapi_app(ServerConfig(transport="unified"))
    health_route = next(r for r in app.routes if getattr(r, "path", None) == "/health")

    async with app.router.lifespan_context(app):
        result = await health_route.endpoint()

    assert result["status"] == "healthy"
    assert result["data_available"] is True
    assert result["data_identity"]


async def test_tool_calls_fail_closed_without_a_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live-but-not-ready host must never answer a data question."""
    from clingen_link.config import settings
    from clingen_link.exceptions import SnapshotUnavailableError
    from clingen_link.mcp.service_adapters import reset_services

    reset_services()
    monkeypatch.setattr(settings, "snapshot_path", str(tmp_path / "absent.sqlite"))

    manager = _manager()
    manager._build_unified_app(ServerConfig(transport="unified"))
    assert manager.app is not None

    async with manager.app.router.lifespan_context(manager.app):
        with pytest.raises(SnapshotUnavailableError):
            manager._service_factory()
