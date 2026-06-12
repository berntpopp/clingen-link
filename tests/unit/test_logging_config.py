"""Tests for clingen_link.logging_config."""

from __future__ import annotations

import logging
import os

import pytest

from clingen_link import logging_config


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """Save and restore the root logger handlers/level around each test."""
    saved_handlers = logging.root.handlers[:]
    saved_level = logging.root.level
    yield
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    for handler in saved_handlers:
        logging.root.addHandler(handler)
    logging.root.setLevel(saved_level)


def test_configure_logging_stdio_uses_stderr_and_suppresses_banner() -> None:
    logging_config.configure_logging("stdio")
    assert logging.root.handlers
    assert os.environ.get("FASTMCP_DISABLE_BANNER") == "1"
    assert os.environ.get("NO_COLOR") == "1"
    assert logging.getLogger("fastmcp").level == logging.WARNING


def test_configure_logging_http_uses_level() -> None:
    logging_config.configure_logging("http", "DEBUG")
    assert logging.root.level == logging.DEBUG


def test_transport_logger_prefixes_message() -> None:
    adapter = logging_config.get_server_logger("unified")
    msg, _kwargs = adapter.process("hello", {})
    assert msg == "[unified] hello"


def test_logger_getters_return_adapters() -> None:
    assert logging_config.get_mcp_logger("stdio") is not None
    assert logging_config.get_api_logger("http") is not None
