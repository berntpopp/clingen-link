#!/usr/bin/env python
"""Unified clingen-link server with multiple transport support.

Single entry point supporting a FastAPI host (/health), MCP HTTP, and MCP STDIO
transports.
"""

import argparse
import asyncio
import sys

from clingen_link.cli import create_config_from_args, create_parser
from clingen_link.exceptions import ConfigurationError, StartupError
from clingen_link.server_manager import UnifiedServerManager


async def async_main(args: argparse.Namespace) -> None:
    """Async entry point for HTTP-based transports."""
    try:
        config = create_config_from_args(args)
        manager = UnifiedServerManager()
        await manager.start_server(config)
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
        sys.exit(0)
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        sys.exit(1)
    except StartupError as e:
        print(f"Startup error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


def main() -> None:
    """Start the clingen-link unified server with the specified transport."""
    parser = create_parser()
    args = parser.parse_args()

    if args.command in {"config", "health", "refresh"}:
        from clingen_link.cli import main as cli_main

        cli_main()
        return

    if args.transport == "stdio":
        try:
            config = create_config_from_args(args)
            manager = UnifiedServerManager()
            asyncio.run(manager.start_stdio_server(config))
        except Exception as e:
            print(f"STDIO server error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
