"""Tests for clingen_link.cli (typer app, GeneFoundry CLI Standard v1)."""

from __future__ import annotations

from importlib.metadata import entry_points

import pytest
import typer
from typer.testing import CliRunner

from clingen_link import __version__
from clingen_link.cli import app

runner = CliRunner()


def _command_names() -> set[str]:
    return {cmd.name or cmd.callback.__name__ for cmd in app.registered_commands}


def test_app_is_typer_instance() -> None:
    assert isinstance(app, typer.Typer)
    assert app.info.name == "clingen-link"


def test_app_exposes_standard_commands() -> None:
    names = _command_names()
    assert {"serve", "config", "health", "version"}.issubset(names)


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code != 0  # no_args_is_help exits non-zero
    assert "Usage" in result.stdout


def test_version_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_config_validate_ok() -> None:
    result = runner.invoke(app, ["config", "--validate"])
    assert result.exit_code == 0
    assert "Configuration is valid" in result.stdout
    assert "clingen-link configuration" in result.stdout


def test_serve_rejects_stdio() -> None:
    result = runner.invoke(app, ["serve", "--transport", "stdio"])
    assert result.exit_code != 0


@pytest.mark.parametrize("transport", ["unified", "http"])
def test_serve_accepts_valid_transports(monkeypatch, transport: str) -> None:
    captured: dict[str, str] = {}

    class _FakeManager:
        async def start_server(self, config) -> None:
            captured["transport"] = config.transport

    import clingen_link.server_manager as sm

    monkeypatch.setattr(sm, "UnifiedServerManager", _FakeManager)
    result = runner.invoke(app, ["serve", "--transport", transport])
    assert result.exit_code == 0
    assert captured["transport"] == transport


def test_serve_rejects_bad_mcp_path() -> None:
    result = runner.invoke(app, ["serve", "--mcp-path", "no-slash"])
    assert result.exit_code != 0


def test_health_handles_connection_failure(monkeypatch) -> None:
    import httpx

    def _boom(*_args, **_kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", _boom)
    result = runner.invoke(app, ["health", "--url", "http://127.0.0.1:9"])
    assert result.exit_code == 1
    assert "Failed to connect" in result.stdout


def test_health_reports_healthy(monkeypatch) -> None:
    import httpx

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"status": "healthy", "transport": "unified"}

    monkeypatch.setattr(httpx, "get", lambda *_a, **_k: _Resp())
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 0
    assert "Server is healthy" in result.stdout


def test_refresh_check_exits_with_handler_code(tmp_path, monkeypatch) -> None:
    from clingen_link.etl import refresh as refresh_mod

    out = tmp_path / "absent.sqlite"
    monkeypatch.setattr(refresh_mod, "gather_sources", lambda **_k: (refresh_mod.Sources(), []))
    result = runner.invoke(app, ["refresh", "--check", "--out", str(out)])
    assert result.exit_code == 1


def test_console_script_entry_resolves() -> None:
    scripts = entry_points(group="console_scripts")
    matching = [ep for ep in scripts if ep.name == "clingen-link"]
    assert matching, "clingen-link console script not registered"
    assert matching[0].value == "clingen_link.cli:app"
    assert matching[0].load() is app
