"""Tests for clingen_link.logging_config (structlog canon)."""

from __future__ import annotations

import json
import logging

import pytest
import structlog

from clingen_link import __version__, logging_config


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """Save and restore root logger handlers/level and structlog config."""
    saved_handlers = logging.root.handlers[:]
    saved_level = logging.root.level
    yield
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    for handler in saved_handlers:
        logging.root.addHandler(handler)
    logging.root.setLevel(saved_level)
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def test_configure_logging_returns_bound_logger() -> None:
    logger = logging_config.configure_logging("INFO", "json")
    assert logger is not None
    assert logging.root.handlers


def test_configure_logging_sets_level() -> None:
    logging_config.configure_logging("DEBUG", "json")
    assert logging.root.level == logging.DEBUG


def test_json_renderer_emits_static_fields(capsys) -> None:
    logging_config.configure_logging("INFO", "json")
    logger = logging_config.get_server_logger()
    logger.info("hello", foo="bar")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["service"] == "clingen-link"
    assert payload["version"] == __version__
    assert payload["event"] == "hello"
    assert payload["foo"] == "bar"
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_console_renderer_human_readable(capsys) -> None:
    logging_config.configure_logging("INFO", "console")
    logger = logging_config.get_server_logger()
    logger.info("readable event")
    out = capsys.readouterr().out
    assert "readable event" in out


def test_correlation_id_merged_via_contextvars(capsys) -> None:
    logging_config.configure_logging("INFO", "json")
    structlog.contextvars.bind_contextvars(correlation_id="abc-123")
    try:
        logging_config.get_server_logger().info("with-cid")
        out = capsys.readouterr().out.strip().splitlines()[-1]
        payload = json.loads(out)
        assert payload["correlation_id"] == "abc-123"
    finally:
        structlog.contextvars.clear_contextvars()


def test_default_format_is_json(capsys) -> None:
    logging_config.configure_logging("INFO")
    logging_config.get_server_logger().info("default-format")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    # JSON renderer produces a parseable object.
    assert json.loads(out)["event"] == "default-format"
