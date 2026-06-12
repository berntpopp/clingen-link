"""Command line interface for the clingen-link server.

Phase 1 provides the argparse parser and config translation used by the
``clingen-link`` entry point (``server:main``), plus ``config`` and ``health``
subcommands. The ETL ``refresh`` subcommand lands in a later phase.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from .config import ServerConfig, settings


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the server."""
    parser = argparse.ArgumentParser(
        description="clingen-link unified server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Transport Options:
  unified  - FastAPI host (/health) + MCP HTTP (default)
  http     - alias for unified
  stdio    - MCP STDIO only (for AI assistants)

Examples:
  uv run python server.py --transport unified --port 8000
  uv run python server.py --transport stdio
        """,
    )
    parser.add_argument(
        "--transport",
        choices=["unified", "http", "stdio"],
        default="unified",
        help="Transport mode (default: unified)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to (default: 8000)")
    parser.add_argument("--mcp-path", default="/mcp", help="MCP endpoint path (default: /mcp)")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--disable-docs", action="store_true", help="Disable API documentation endpoints"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    config_parser = subparsers.add_parser("config", help="Show configuration")
    config_parser.add_argument("--validate", action="store_true", help="Validate configuration")

    health_parser = subparsers.add_parser("health", help="Check server health")
    health_parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="Server URL to check (default: http://127.0.0.1:8000)",
    )

    return parser


def create_config_from_args(args: argparse.Namespace) -> ServerConfig:
    """Create server configuration from command line arguments."""
    return ServerConfig(
        transport=args.transport,
        host=args.host,
        port=args.port,
        mcp_path=args.mcp_path,
        enable_docs=not args.disable_docs,
        log_level=args.log_level,
    )


def handle_config_command(args: argparse.Namespace) -> None:
    """Handle the config subcommand."""
    config = create_config_from_args(args)
    print("=== clingen-link Configuration ===")
    print(f"Transport: {config.transport}")
    print(f"Host: {config.host}")
    print(f"Port: {config.port}")
    print(f"MCP Path: {config.mcp_path}")
    print(f"Log Level: {config.log_level}")
    print()
    print("=== Environment Settings ===")
    print(f"validity_api_base: {settings.validity_api_base}")
    print(f"dosage_ftp_base: {settings.dosage_ftp_base}")
    print(f"actionability_api_base: {settings.actionability_api_base}")
    print(f"erepo_api_base: {settings.erepo_api_base}")
    print(f"snapshot_path: {settings.snapshot_path}")
    print()
    if args.validate:
        print("=== Configuration Validation ===")
        if config.port < 1 or config.port > 65535:
            print("Invalid port number")
            sys.exit(1)
        if not config.mcp_path.startswith("/"):
            print("MCP path must start with '/'")
            sys.exit(1)
        print("Configuration is valid")


def handle_health_command(args: argparse.Namespace) -> None:
    """Handle the health subcommand."""
    try:
        response = httpx.get(f"{args.url}/health", timeout=5)
    except httpx.HTTPError as e:
        print(f"Failed to connect to server: {e}")
        sys.exit(1)
    if response.status_code == 200:
        data = response.json()
        print("Server is healthy")
        print(f"Transport: {data.get('transport', 'unknown')}")
        print(f"Status: {data.get('status', 'unknown')}")
    else:
        print(f"Server returned status {response.status_code}")
        sys.exit(1)


def main() -> None:
    """Execute CLI subcommands."""
    parser = create_parser()
    args = parser.parse_args()
    if args.command == "config":
        handle_config_command(args)
    elif args.command == "health":
        handle_health_command(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
