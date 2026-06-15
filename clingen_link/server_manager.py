"""Unified server manager for clingen-link."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, cast

import uvicorn
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP

from clingen_link import __version__
from clingen_link.config import ServerConfig, settings
from clingen_link.exceptions import ConfigurationError, MCPIntegrationError, StartupError
from clingen_link.logging_config import configure_logging, get_server_logger
from clingen_link.mcp.facade import create_clingen_mcp
from clingen_link.mcp.service_adapters import ClingenServices, get_services


class UnifiedServerManager:
    def __init__(self) -> None:
        self.app: FastAPI | None = None
        self.mcp: FastMCP | None = None
        self.shutdown_event = asyncio.Event()
        self.logger: Any = None
        self._current_transport = "unknown"

    # ---------------- service factory helpers ----------------

    def _create_services(self) -> ClingenServices:
        return get_services()

    # ---------------- FastAPI host (health only) ----------------

    def create_app(self) -> FastAPI:
        """Build the FastAPI host application (used by gunicorn/uvicorn)."""
        config = ServerConfig.from_env()
        self._current_transport = "unified"
        return self._build_fastapi_app(config)

    async def _create_fastapi_app(self, config: ServerConfig) -> FastAPI:
        return self._build_fastapi_app(config)

    def _build_fastapi_app(self, config: ServerConfig) -> FastAPI:
        @asynccontextmanager
        async def lifespan(app: FastAPI) -> Any:
            self.logger.info("Starting clingen-link host application...")
            app.state.clingen_services = self._create_services()
            self.logger.info("Services ready")
            try:
                yield
            finally:
                self.logger.info("Shutting down host application...")

        app = FastAPI(
            title="clingen-link MCP Host",
            description="Thin FastAPI host that exposes /health and mounts the MCP HTTP app at /mcp.",
            version=__version__,
            lifespan=lifespan,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        # Bind a correlation id per request and expose it to structlog via
        # contextvars (merged into every log event by ``merge_contextvars``).
        app.add_middleware(CorrelationIdMiddleware)

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "healthy", "transport": self._current_transport}

        return app

    # ---------------- MCP creation ----------------

    def _create_mcp_server(self, service_factory: Callable[[], ClingenServices]) -> FastMCP:
        try:
            mcp = create_clingen_mcp(service_factory=service_factory)
            self.logger.info("MCP facade created")
            return mcp
        except Exception as e:
            raise MCPIntegrationError(f"Failed to create MCP server: {e}", "mcp") from e

    @staticmethod
    def _compose_lifespan(app: FastAPI, mcp_app: Any) -> None:
        fastapi_lifespan = app.router.lifespan_context
        mcp_lifespan = mcp_app.lifespan

        @asynccontextmanager
        async def combined(parent_app: FastAPI) -> Any:
            async with fastapi_lifespan(parent_app), mcp_lifespan(mcp_app):
                yield

        app.router.lifespan_context = combined

    # ---------------- signal handlers ----------------

    def _setup_signal_handlers(self) -> None:
        def handler(signum: int, _frame: Any) -> None:
            self.logger.info(f"Received signal {signum}; shutting down...")
            self.shutdown_event.set()

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    # ---------------- entry points ----------------

    async def start_unified_server(self, config: ServerConfig) -> None:
        try:
            self._current_transport = "unified"
            log_format = "console" if config.dev else settings.LOG_FORMAT
            configure_logging(config.log_level, log_format)
            self.logger = get_server_logger()

            self.app = await self._create_fastapi_app(config)

            def service_factory() -> ClingenServices:
                if self.app is None:
                    raise RuntimeError("FastAPI host not initialized")
                return cast(ClingenServices, self.app.state.clingen_services)

            self.mcp = self._create_mcp_server(service_factory)
            mcp_http_app = self.mcp.http_app(path="/", stateless_http=True, json_response=True)
            self._compose_lifespan(self.app, mcp_http_app)
            self.app.mount(config.mcp_path, mcp_http_app)

            self.logger.info(f"MCP HTTP at http://{config.host}:{config.port}{config.mcp_path}")
            self.logger.info(f"Health at http://{config.host}:{config.port}/health")

            self._setup_signal_handlers()

            uvicorn_config = uvicorn.Config(
                app=self.app,
                host=config.host,
                port=config.port,
                log_level=config.log_level.lower(),
                access_log=True,
            )
            await uvicorn.Server(uvicorn_config).serve()
        except Exception as e:
            raise StartupError(f"Failed to start unified server: {e}", "unified") from e

    async def start_http_server(self, config: ServerConfig) -> None:
        """Alias for the unified server (FastAPI host + mounted MCP HTTP)."""
        await self.start_unified_server(config)

    async def start_server(self, config: ServerConfig) -> None:
        if config.transport in {"unified", "http"}:
            await self.start_unified_server(config)
        else:
            raise ConfigurationError(f"Unknown transport: {config.transport}")


def create_app() -> FastAPI:
    """Module-level ASGI factory for gunicorn/uvicorn (clingen_link.server_manager:create_app)."""
    manager = UnifiedServerManager()
    manager._current_transport = "unified"
    configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    manager.logger = get_server_logger()
    return manager.create_app()
