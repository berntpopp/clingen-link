"""Unified server manager for clingen-link."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, cast

# FastMCP >=3.4.3 enables a global localhost-only default that rejects the public
# proxy Host before application allowlists can apply. Disable that implicit guard;
# the manager installs explicit outer and native guards with the same exact lists.
import fastmcp
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

if hasattr(fastmcp.settings, "http_host_origin_protection"):
    fastmcp.settings.http_host_origin_protection = False


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
        from fastmcp.server.http import HostOriginGuardMiddleware

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
        cors_origins = settings.cors_origins_list
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            # Never send credentials with a wildcard origin: the browser rejects
            # `Access-Control-Allow-Credentials: true` paired with `*`, and doing
            # so would also be unsafe. Credentials stay enabled for an explicit
            # allow-list (e.g. localhost dev).
            allow_credentials=cors_origins != ["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        # Bind a correlation id per request and expose it to structlog via
        # contextvars (merged into every log event by ``merge_contextvars``).
        app.add_middleware(CorrelationIdMiddleware)
        app.add_middleware(
            HostOriginGuardMiddleware,
            allowed_hosts=settings.ALLOWED_HOSTS,
            allowed_origins=settings.ALLOWED_ORIGINS,
            mode="strict",
        )

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {
                "status": "healthy",
                "version": __version__,
                "transport": "streamable-http-stateless",
            }

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
        mcp_lifespan = mcp_app.router.lifespan_context

        @asynccontextmanager
        async def combined(parent_app: FastAPI) -> Any:
            async with fastapi_lifespan(parent_app), mcp_lifespan(parent_app):
                yield

        app.router.lifespan_context = combined

    def _build_unified_app(self, config: ServerConfig) -> FastAPI:
        self.app = self._build_fastapi_app(config)

        def service_factory() -> ClingenServices:
            if self.app is None:
                raise RuntimeError("FastAPI host not initialized")
            return cast(ClingenServices, self.app.state.clingen_services)

        self.mcp = self._create_mcp_server(service_factory)
        mcp_http_app = self.mcp.http_app(
            path=config.mcp_path,
            stateless_http=True,
            json_response=True,
            host_origin_protection=True,
            allowed_hosts=settings.ALLOWED_HOSTS,
            allowed_origins=settings.ALLOWED_ORIGINS,
        )
        self._compose_lifespan(self.app, mcp_http_app)
        self.app.mount("/", mcp_http_app)
        return self.app

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

            self.app = self._build_unified_app(config)

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


def create_unified_app() -> FastAPI:
    """Module-level guarded unified ASGI factory for tests and hosted runners."""
    manager = UnifiedServerManager()
    manager._current_transport = "unified"
    configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    manager.logger = get_server_logger()
    return manager._build_unified_app(ServerConfig.from_env())
