#!/usr/bin/env python
"""MCP STDIO server for clingen-link.

Backwards-compatible STDIO entry point for AI assistants like Claude Desktop.
A thin wrapper around the unified server architecture. Banner/color suppression
and stderr-only logging are handled by the transport-aware logging config so the
JSON-RPC framing on stdout stays clean.
"""

import asyncio
import os
import sys

from clingen_link.config import ServerConfig
from clingen_link.server_manager import UnifiedServerManager

# Suppress FastMCP/uvicorn banners and color before anything imports them, so
# nothing pollutes the stdout JSON-RPC stream.
os.environ.setdefault("FASTMCP_DISABLE_BANNER", "1")
os.environ.setdefault("NO_COLOR", "1")
os.environ.setdefault("TERM", "dumb")


def main() -> None:
    """Start the STDIO MCP server for AI-assistant integration."""
    try:
        config = ServerConfig(
            transport="stdio",
            host="127.0.0.1",  # not used for STDIO
            port=8000,  # not used for STDIO
            mcp_path="/mcp",  # not used for STDIO
            enable_docs=False,
            log_level="WARNING",  # minimal logging for STDIO
        )
        manager = UnifiedServerManager()
        asyncio.run(manager.start_stdio_server(config))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        # Errors go to stderr so they do not interfere with the STDIO protocol.
        print(f"MCP server error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
