"""Tests for clingen_link.cli (argparse parser + config translation)."""

from __future__ import annotations

import pytest

from clingen_link.cli import (
    create_config_from_args,
    create_parser,
    handle_config_command,
    main,
)


def test_parser_defaults() -> None:
    parser = create_parser()
    args = parser.parse_args([])
    assert args.transport == "unified"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.mcp_path == "/mcp"


def test_parser_transport_choice() -> None:
    parser = create_parser()
    args = parser.parse_args(["--transport", "stdio"])
    assert args.transport == "stdio"


def test_create_config_from_args() -> None:
    parser = create_parser()
    args = parser.parse_args(["--transport", "http", "--port", "9001", "--disable-docs"])
    config = create_config_from_args(args)
    assert config.transport == "http"
    assert config.port == 9001
    assert config.enable_docs is False


def test_config_command_prints(capsys) -> None:
    parser = create_parser()
    args = parser.parse_args(["config", "--validate"])
    handle_config_command(args)
    out = capsys.readouterr().out
    assert "clingen-link Configuration" in out
    assert "Configuration is valid" in out


def test_main_no_command_prints_help(capsys) -> None:
    import sys

    original = sys.argv
    try:
        sys.argv = ["clingen-link"]
        main()
    finally:
        sys.argv = original
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_config_command_invalid_port_exits(capsys) -> None:
    parser = create_parser()
    args = parser.parse_args(["config", "--validate"])
    args.port = 70000
    with pytest.raises(SystemExit):
        handle_config_command(args)


def test_parser_has_refresh_subcommand() -> None:
    parser = create_parser()
    args = parser.parse_args(["refresh", "--check"])
    assert args.command == "refresh"
    assert args.check is True
    assert args.out is None


def test_refresh_command_exits_with_handler_code(tmp_path, monkeypatch) -> None:
    from clingen_link.cli import handle_refresh_command
    from clingen_link.etl import refresh as refresh_mod

    out = tmp_path / "absent.sqlite"
    parser = create_parser()
    args = parser.parse_args(["refresh", "--check", "--out", str(out)])
    monkeypatch.setattr(refresh_mod, "gather_sources", lambda: (refresh_mod.Sources(), []))
    with pytest.raises(SystemExit) as exc:
        handle_refresh_command(args)
    assert exc.value.code == 1
