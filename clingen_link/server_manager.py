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
from fastapi.responses import JSONResponse
from fastmcp import FastMCP

from clingen_link import __version__
from clingen_link.config import ServerConfig, settings
from clingen_link.exceptions import (
    ConfigurationError,
    MCPIntegrationError,
    SnapshotUnavailableError,
    StartupError,
)
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

    def _service_factory(self) -> ClingenServices:
        """Return the request-shared services, failing closed when data is absent.

        Raises:
            SnapshotUnavailableError: no verified reference snapshot is selected. The
                MCP error boundary maps this to the canonical ``snapshot_unavailable``
                envelope, so a not-ready host declines to answer instead of guessing.
        """
        if self.app is None:
            raise RuntimeError("FastAPI host not initialized")
        services = getattr(self.app.state, "clingen_services", None)
        if services is None:
            # Retry construction: the init sidecar may have selected the snapshot after
            # this host started. A still-absent snapshot re-raises, fail-closed.
            services = self._create_services()
            self.app.state.clingen_services = services
            self.app.state.clingen_data_error = None
        return cast(ClingenServices, services)

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
            # The production image is code-only: the reference snapshot is materialized
            # into a mounted volume by the `clingen-data-init` sidecar, never baked in.
            # A host that starts before (or without) that materialization is LIVE but NOT
            # READY -- it must still serve MCP definitions, so record the fault instead of
            # aborting the process. /health then reports 503 `degraded` and every
            # data-bearing tool call fails closed with a `snapshot_unavailable` envelope.
            app.state.clingen_services = None
            app.state.clingen_data_error = None
            try:
                app.state.clingen_services = self._create_services()
                self.logger.info("Services ready")
            except SnapshotUnavailableError as exc:
                app.state.clingen_data_error = str(exc)
                self.logger.error(f"ClinGen reference snapshot unavailable: {exc}")
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
        async def health() -> Any:
            result: dict[str, Any] = {
                "status": "healthy",
                "version": __version__,
                "transport": "streamable-http-stateless",
            }
            services = getattr(app.state, "clingen_services", None)
            if services is None:
                # Readiness, not liveness: never report healthy to a proxy or an
                # orchestrator while the authoritative snapshot is not selected.
                result["status"] = "degraded"
                result["data_available"] = False
                reason = getattr(app.state, "clingen_data_error", None)
                if reason:
                    result["reason"] = reason
                return JSONResponse(result, status_code=503)
            result["data_available"] = True
            result["data_identity"] = services.store.data_identity
            return result

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
        self.mcp = self._create_mcp_server(self._service_factory)
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
