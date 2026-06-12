"""Configuration settings for the clingen-link server.

Settings load from the environment with the ``CLINGEN_LINK_`` prefix (and an
optional ``.env`` file). A module-level ``settings`` singleton is the single
source of truth; ``ServerConfig`` is a lightweight dataclass for transport
selection passed around the server manager and CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default location of the bundled read-only snapshot, relative to this package.
_DEFAULT_SNAPSHOT_PATH = str(Path(__file__).resolve().parent / "data" / "clingen.sqlite.zst")


@dataclass
class ServerConfig:
    """Server configuration with transport selection."""

    transport: Literal["unified", "http", "stdio"] = "unified"
    host: str = "127.0.0.1"
    port: int = 8000
    mcp_path: str = "/mcp"
    enable_docs: bool = True
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> ServerConfig:
        """Create configuration from environment-backed settings."""
        return cls(
            transport=settings.MCP_TRANSPORT,
            host=settings.MCP_HOST,
            port=settings.MCP_PORT,
            mcp_path=settings.MCP_PATH,
            enable_docs=settings.ENABLE_SWAGGER,
            log_level=settings.LOG_LEVEL,
        )


class Settings(BaseSettings):
    """Application settings (env prefix ``CLINGEN_LINK_``)."""

    # ---- ClinGen upstream endpoints (live drill-down + ETL sources) ----
    validity_api_base: str = "https://search.clinicalgenome.org/api"
    dosage_ftp_base: str = "https://ftp.clinicalgenome.org"
    actionability_api_base: str = "https://actionability.clinicalgenome.org/ac"
    erepo_api_base: str = "https://erepo.clinicalgenome.org/evrepo"
    # HGNC complete-set TSV (authoritative symbol/alias/prev-symbol/name table; ETL-only).
    hgnc_complete_set_url: str = (
        "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
    )

    # ---- Snapshot ----
    snapshot_path: str = _DEFAULT_SNAPSHOT_PATH

    # ---- Live client resilience ----
    # Max concurrent in-flight upstream requests; bounds burst pressure.
    max_concurrency: int = 5
    # Per-request upstream timeout (seconds).
    request_timeout_s: int = 30
    # Max seconds a request waits for a concurrency slot before returning fast,
    # retryable backpressure (rate_limited) instead of hanging.
    queue_wait_timeout_s: int = 20

    # ---- Caching ----
    cache_size: int = 512
    cache_ttl_minutes: int = 60
    # ERepo live drill-down is keyed to the ERepo `news` version and changes
    # infrequently, so it gets a longer default TTL than the general cache.
    erepo_cache_ttl_minutes: int = 720

    # ---- Transport ----
    MCP_TRANSPORT: Literal["unified", "http", "stdio"] = "unified"
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8000
    MCP_PATH: str = "/mcp"

    # ---- Logging ----
    LOG_LEVEL: str = "INFO"
    MCP_LOG_LEVEL: str = "INFO"
    STDIO_LOG_LEVEL: str = "WARNING"  # reduced for STDIO compatibility

    # ---- Server ----
    CORS_ORIGINS: str = "*"  # comma-separated list of allowed origins
    ENABLE_SWAGGER: bool = True
    ENABLE_MONITORING: bool = True
    GRACEFUL_SHUTDOWN_TIMEOUT: int = 30
    MAX_PAGE_SIZE: int = 100

    model_config = SettingsConfigDict(
        env_prefix="CLINGEN_LINK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("MCP_PATH")
    @classmethod
    def _validate_mcp_path(cls, v: str) -> str:
        """Ensure the MCP path starts with a slash."""
        return v if v.startswith("/") else f"/{v}"

    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins as a list."""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def mcp_url(self) -> str:
        """Return the full MCP URL."""
        return f"http://{self.MCP_HOST}:{self.MCP_PORT}{self.MCP_PATH}"


# Global settings instance
settings = Settings()
