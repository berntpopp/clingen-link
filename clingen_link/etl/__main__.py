"""``python -m clingen_link.etl`` entry point.

Supports ``python -m clingen_link.etl refresh [--check] [--out PATH]`` so the
ETL can be driven without the console script. Any first token other than
``refresh`` (or none) prints usage.
"""

from __future__ import annotations

import argparse
import sys

from .refresh import add_refresh_arguments, handle_refresh


def main(argv: list[str] | None = None) -> int:
    """Parse ``etl`` subcommands and dispatch to the refresh handler."""
    parser = argparse.ArgumentParser(prog="python -m clingen_link.etl")
    subparsers = parser.add_subparsers(dest="command")
    refresh_parser = subparsers.add_parser("refresh", help="Build or check the snapshot.")
    add_refresh_arguments(refresh_parser)

    args = parser.parse_args(argv)
    if args.command == "refresh":
        return handle_refresh(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
