"""Logging configuration for the clingen-link server.

Transport-aware: stdio sends all logging to stderr (so JSON-RPC framing on
stdout stays clean) and suppresses FastMCP/uvicorn banners and color via the
environment. HTTP transports log to stdout at the configured level.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from .config import settings


class TransportAwareFormatter(logging.Formatter):
    """Formatter that includes transport context in log messages."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record, prefixing the transport name when available."""
        if hasattr(record, "transport"):
            record.msg = f"[{record.transport}] {record.msg}"
        return super().format(record)


def _configure_stdio_environment() -> None:
    """Suppress banners and color so stdout carries only clean JSON-RPC framing."""
    os.environ.setdefault("FASTMCP_DISABLE_BANNER", "1")
    os.environ.setdefault("NO_COLOR", "1")
    os.environ.setdefault("TERM", "dumb")


def configure_logging(transport: str, level: str | None = None) -> None:
    """Configure logging for a specific transport."""
    if level is None:
        level = settings.STDIO_LOG_LEVEL if transport == "stdio" else settings.MCP_LOG_LEVEL

    for existing in logging.root.handlers[:]:
        logging.root.removeHandler(existing)

    formatter = TransportAwareFormatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler: logging.Handler
    if transport == "stdio":
        # STDIO transport: only stderr, minimal logging, banners suppressed.
        _configure_stdio_environment()
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.WARNING)

        logging.getLogger("fastmcp").setLevel(logging.WARNING)
        logging.getLogger("fastmcp.utilities.openapi").setLevel(logging.WARNING)
        logging.getLogger("uvicorn").setLevel(logging.WARNING)
        logging.getLogger("fastapi").setLevel(logging.WARNING)
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, level.upper()))

    handler.setFormatter(formatter)

    logging.root.setLevel(getattr(logging, level.upper()))
    logging.root.addHandler(handler)


def get_transport_logger(name: str, transport: str) -> Any:
    """Get a logger that prefixes messages with the transport context."""
    logger = logging.getLogger(name)

    class TransportLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
        def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
            return f"[{transport}] {msg}", kwargs

    return TransportLoggerAdapter(logger, {})


def get_server_logger(transport: str) -> Any:
    """Get the server logger with transport context."""
    return get_transport_logger("clingen_server", transport)


def get_mcp_logger(transport: str) -> Any:
    """Get the MCP logger with transport context."""
    return get_transport_logger("clingen_mcp", transport)


def get_api_logger(transport: str) -> Any:
    """Get the API logger with transport context."""
    return get_transport_logger("clingen_api", transport)
