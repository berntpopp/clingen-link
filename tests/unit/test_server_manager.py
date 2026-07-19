"""Tests for clingen_link.server_manager.UnifiedServerManager."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastmcp import FastMCP

from clingen_link.config import ServerConfig, settings
from clingen_link.exceptions import ConfigurationError
from clingen_link.mcp.service_adapters import ClingenServices, set_services
from clingen_link.runtime_data_identity import (
    build_identity_manifest,
    canonical_json_bytes,
    verify_runtime_identity,
)
from clingen_link.server_manager import UnifiedServerManager
from clingen_link.store.db import canonical_expanded_digest


def _manager() -> UnifiedServerManager:
    manager = UnifiedServerManager()
    manager.logger = logging.getLogger("test.clingen")
    return manager


def _write_selected_version(root: Path, name: str, source: Path) -> tuple[Path, dict[str, str]]:
    version = root / name
    version.mkdir()
    snapshot = version / "clingen.sqlite"
    shutil.copyfile(source, snapshot)
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE selected_version (marker TEXT NOT NULL)")
        connection.execute("INSERT INTO selected_version VALUES (?)", (name,))
    release_tag = f"data-clingen-{name}"
    manifest = build_identity_manifest(version, release_tag, [snapshot])
    (version / "data-identity-manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    return version, verify_runtime_identity(version)


def _select_version(root: Path, version: Path) -> None:
    staged = root / ".current.tmp"
    staged.symlink_to(version.name, target_is_directory=True)
    os.replace(staged, root / "current")


@pytest.fixture
def injected_services(
    test_snapshot_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ClingenServices]:
    """Inject a real services container built from the small test snapshot."""
    data_root = tmp_path / "materialized"
    data_root.mkdir()
    snapshot = data_root / "clingen.sqlite"
    shutil.copyfile(test_snapshot_path, snapshot)
    release_tag = "data-clingen-2026-07-16"
    manifest = build_identity_manifest(data_root, release_tag, [snapshot])
    (data_root / "data-identity-manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    actual = verify_runtime_identity(data_root)
    monkeypatch.setattr(settings, "data_release_tag", release_tag)
    monkeypatch.setattr(settings, "data_identity_digest", actual["digest"])
    services = ClingenServices.from_snapshot(snapshot)
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


async def test_health_emits_runtime_v1_release_identity(
    injected_services: ClingenServices,
) -> None:
    manager = _manager()
    app = await manager._create_fastapi_app(ServerConfig(transport="unified"))
    health_route = next(r for r in app.routes if getattr(r, "path", None) == "/health")

    async with app.router.lifespan_context(app):
        result = await health_route.endpoint()

    identity = result["release_identity"]["data_identity"]
    assert result["status"] == "healthy"
    assert identity["expected"] == identity["actual"]
    assert identity["actual"]["digest"].startswith("sha256:")


async def test_health_degrades_without_partial_identity_when_runtime_data_is_corrupt(
    injected_services: ClingenServices,
) -> None:
    manager = _manager()
    app = await manager._create_fastapi_app(ServerConfig(transport="unified"))
    health_route = next(r for r in app.routes if getattr(r, "path", None) == "/health")
    snapshot = injected_services.store._db_path

    async with app.router.lifespan_context(app):
        snapshot.chmod(0o644)
        content = bytearray(snapshot.read_bytes())
        content[len(content) // 2] ^= 0x01
        snapshot.write_bytes(content)
        response = await health_route.endpoint()

    assert response.status_code == 503
    body = __import__("json").loads(bytes(response.body))
    assert body["status"] == "degraded"
    assert "release_identity" not in body


async def test_health_attests_the_store_bound_version_until_process_restart(
    test_snapshot_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_a, _identity_a = _write_selected_version(tmp_path, "a", test_snapshot_path)
    version_b, identity_b = _write_selected_version(tmp_path, "b", test_snapshot_path)
    _select_version(tmp_path, version_a)
    services_a = ClingenServices.from_snapshot(
        tmp_path / "current" / "clingen.sqlite", data_root=tmp_path
    )
    assert all(
        service._store is services_a.store
        for service in (
            services_a.validity,
            services_a.dosage,
            services_a.actionability,
            services_a.erepo,
            services_a.cspec,
            services_a.gene,
        )
    )
    set_services(services_a)
    _select_version(tmp_path, version_b)
    monkeypatch.setattr(settings, "data_release_tag", identity_b["release_tag"])
    monkeypatch.setattr(settings, "data_identity_digest", identity_b["digest"])
    app = await _manager()._create_fastapi_app(ServerConfig(transport="unified"))
    health_route = next(r for r in app.routes if getattr(r, "path", None) == "/health")

    try:
        async with app.router.lifespan_context(app):
            assert app.state.clingen_services is services_a
            response = await health_route.endpoint()

        assert response.status_code == 503
        body = __import__("json").loads(bytes(response.body))
        assert body["status"] == "degraded"
        assert "release_identity" not in body
        with services_a.store.connection() as connection:
            assert connection.execute("SELECT marker FROM selected_version").fetchone()[0] == "a"

        services_b = ClingenServices.from_snapshot(
            tmp_path / "current" / "clingen.sqlite", data_root=tmp_path
        )
        set_services(services_b)
        restarted = await _manager()._create_fastapi_app(ServerConfig(transport="unified"))
        restarted_health = next(
            r for r in restarted.routes if getattr(r, "path", None) == "/health"
        )
        async with restarted.router.lifespan_context(restarted):
            result = await restarted_health.endpoint()
        assert result["status"] == "healthy"
        assert result["release_identity"]["data_identity"]["actual"] == identity_b
        services_b.store.close()
    finally:
        services_a.store.close()


def test_explicit_data_root_is_preserved_when_snapshot_path_comes_from_settings(
    test_snapshot_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = tmp_path / "version"
    version.mkdir()
    snapshot = version / "clingen.sqlite"
    shutil.copyfile(test_snapshot_path, snapshot)
    expanded = canonical_expanded_digest(snapshot, member_name="clingen.sqlite")
    compressed = "1" * 64
    identity = {
        "mode": "external-reference",
        "compressed_sha256": compressed,
        "expanded_tree_sha256": expanded,
        "schema_version": "2.0.0",
        "schema_minimum": "2.0.0",
        "schema_maximum": "2.0.0",
        "compressed_bytes": 1,
        "expanded_bytes": snapshot.stat().st_size,
    }
    identity_path = version / "identity.json"
    identity_path.write_text(
        __import__("json").dumps(identity, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest = build_identity_manifest(version, "data-clingen-test", [snapshot, identity_path])
    (version / "data-identity-manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    (tmp_path / "current").symlink_to(version.name, target_is_directory=True)
    monkeypatch.setattr(settings, "snapshot_path", str(tmp_path / "current" / "clingen.sqlite"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path / "wrong-root"))
    monkeypatch.setattr(settings, "data_bundle_path", str(tmp_path / "bundle.zst"))
    monkeypatch.setattr(settings, "data_bundle_sha256", compressed)
    monkeypatch.setattr(settings, "data_expanded_sha256", expanded)

    services = ClingenServices.from_snapshot(data_root=tmp_path)

    try:
        assert services.store.materialized_root == version.resolve()
    finally:
        services.store.close()


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
