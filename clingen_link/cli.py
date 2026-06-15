"""Command line interface for clingen-link (GeneFoundry CLI Standard v1).

A single ``typer`` application exposing ``serve``, ``config``, ``health``,
``version``, and the ETL ``refresh`` command. The console script
``clingen-link`` resolves to :data:`app`; there is no bare-serve and no stdio
transport (Streamable HTTP only).
"""

from __future__ import annotations

import asyncio

import httpx
import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import ServerConfig, settings

app = typer.Typer(
    name="clingen-link",
    add_completion=False,
    no_args_is_help=True,
    help="clingen-link — MCP server grounding gene/disease/variant questions in ClinGen.",
)

console = Console()

TransportOption = typer.Option("unified", "--transport", help="Transport mode (unified or http).")


@app.command()
def serve(
    transport: str = TransportOption,
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to."),
    port: int = typer.Option(8000, "--port", help="Port to bind to."),
    mcp_path: str = typer.Option("/mcp", "--mcp-path", help="MCP endpoint path."),
    log_level: str = typer.Option("INFO", "--log-level", help="Log level."),
    disable_docs: bool = typer.Option(False, "--disable-docs", help="Disable API docs."),
    dev: bool = typer.Option(False, "--dev", help="Development mode (console logs, reload-ready)."),
) -> None:
    """Start the unified FastAPI host (/health) with the MCP HTTP app at /mcp."""
    if transport not in {"unified", "http"}:
        console.print(f"[red]Invalid transport {transport!r}; choose 'unified' or 'http'.[/red]")
        raise typer.Exit(code=2)
    if not mcp_path.startswith("/"):
        console.print("[red]MCP path must start with '/'.[/red]")
        raise typer.Exit(code=2)

    config = ServerConfig(
        transport="unified" if transport == "unified" else "http",
        host=host,
        port=port,
        mcp_path=mcp_path,
        enable_docs=not disable_docs,
        log_level=log_level,
        dev=dev,
    )

    from .server_manager import UnifiedServerManager

    manager = UnifiedServerManager()
    try:
        asyncio.run(manager.start_server(config))
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutdown requested by user[/yellow]")
        raise typer.Exit(code=0) from None


@app.command()
def config(
    validate: bool = typer.Option(False, "--validate", help="Validate configuration."),
) -> None:
    """Show (and optionally validate) the resolved configuration."""
    cfg = ServerConfig.from_env()

    table = Table(title="clingen-link configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("transport", cfg.transport)
    table.add_row("host", cfg.host)
    table.add_row("port", str(cfg.port))
    table.add_row("mcp_path", cfg.mcp_path)
    table.add_row("enable_docs", str(cfg.enable_docs))
    table.add_row("log_level", cfg.log_level)
    table.add_row("log_format", settings.LOG_FORMAT)
    table.add_row("validity_api_base", settings.validity_api_base)
    table.add_row("dosage_ftp_base", settings.dosage_ftp_base)
    table.add_row("actionability_api_base", settings.actionability_api_base)
    table.add_row("erepo_api_base", settings.erepo_api_base)
    table.add_row("snapshot_path", settings.snapshot_path)
    console.print(table)

    if validate:
        if cfg.port < 1 or cfg.port > 65535:
            console.print("[red]Invalid port number[/red]")
            raise typer.Exit(code=1)
        if not cfg.mcp_path.startswith("/"):
            console.print("[red]MCP path must start with '/'[/red]")
            raise typer.Exit(code=1)
        console.print("[green]Configuration is valid[/green]")


@app.command()
def health(
    url: str = typer.Option("http://127.0.0.1:8000", "--url", help="Server base URL to check."),
) -> None:
    """Check the running server's /health endpoint."""
    try:
        response = httpx.get(f"{url}/health", timeout=5)
    except httpx.HTTPError as exc:
        console.print(f"[red]Failed to connect to server: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    if response.status_code != 200:
        console.print(f"[red]Server returned status {response.status_code}[/red]")
        raise typer.Exit(code=1)
    data = response.json()
    console.print("[green]Server is healthy[/green]")
    console.print(f"Transport: {data.get('transport', 'unknown')}")
    console.print(f"Status: {data.get('status', 'unknown')}")


@app.command()
def refresh(
    check: bool = typer.Option(
        False, "--check", help="Dry-run: report snapshot staleness, write nothing."
    ),
    out: str | None = typer.Option(
        None, "--out", help="Snapshot output path (default: bundled snapshot)."
    ),
) -> None:
    """Build or check the bundled ClinGen SQLite snapshot."""
    from pathlib import Path

    from .etl.build import default_snapshot_path
    from .etl.refresh import run_check, run_refresh

    out_path = Path(out) if out else default_snapshot_path()
    code = run_check(out_path) if check else run_refresh(out_path)
    raise typer.Exit(code=code)


@app.command()
def version() -> None:
    """Print the clingen-link version."""
    console.print(__version__)


if __name__ == "__main__":
    app()
