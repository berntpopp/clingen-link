"""Configuration settings for the clingen-link server.

Settings load from the environment with the ``CLINGEN_LINK_`` prefix (and an
optional ``.env`` file). A module-level ``settings`` singleton is the single
source of truth; ``ServerConfig`` is a lightweight dataclass for transport
selection passed around the server manager and CLI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .data_contract import SNAPSHOT_SCHEMA_SEMVER

# The reference volume is mounted at /data — the only approved writable/readable data
# path in the container hardening policy. `current` is the atomically selected version.
_DEFAULT_DATA_ROOT = "/data"
_DEFAULT_SNAPSHOT_PATH = f"{_DEFAULT_DATA_ROOT}/current/clingen.sqlite"
_DEFAULT_DATA_RELEASE_TAG = "data-clingen-de5f403028d2e1e1"
_DEFAULT_DATA_IDENTITY_DIGEST = (
    "sha256:2386237724d0470fc8507202f0c8ecc79390935e938b1a916e26df85b8d53b44"
)
_RELEASE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MUTABLE_RELEASE_TAGS = frozenset({"latest", "main", "master", "head", "stable", "current"})
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class DataRequirement(BaseModel):
    """Exact deployment identity for one immutable external ClinGen snapshot."""

    model_config = ConfigDict(frozen=True)

    bundle_path: Path
    release_tag: str
    compressed_sha256: str
    expanded_tree_sha256: str
    schema_version: str
    schema_minimum: str
    schema_maximum: str
    max_compressed_bytes: int
    max_expanded_bytes: int

    @field_validator("release_tag")
    @classmethod
    def _validate_release_tag(cls, value: str) -> str:
        if not _RELEASE_TAG.fullmatch(value) or value.lower() in _MUTABLE_RELEASE_TAGS:
            raise ValueError("release tag must be an immutable release identifier")
        return value

    @field_validator("compressed_sha256", "expanded_tree_sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        digest = value.removeprefix("sha256:").lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("expected a 64-character SHA-256 digest")
        return digest

    @field_validator("schema_version", "schema_minimum", "schema_maximum")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise ValueError("schema version must be semantic X.Y.Z")
        return value

    @model_validator(mode="after")
    def _validate_schema_compatibility(self) -> DataRequirement:
        def version(value: str) -> tuple[int, int, int]:
            return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]

        if (
            not version(self.schema_minimum)
            <= version(self.schema_version)
            <= version(self.schema_maximum)
        ):
            raise ValueError("actual schema version is outside the compatible range")
        return self

    @field_validator("max_compressed_bytes", "max_expanded_bytes")
    @classmethod
    def _validate_ceiling(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("data size ceiling must be positive")
        return value


@dataclass
class ServerConfig:
    """Server configuration with transport selection.

    Streamable HTTP only — ``unified`` (FastAPI host + mounted MCP HTTP) and its
    ``http`` alias. There is no stdio transport.
    """

    transport: Literal["unified", "http"] = "unified"
    host: str = "127.0.0.1"
    port: int = 8000
    mcp_path: str = "/mcp"
    enable_docs: bool = True
    log_level: str = "INFO"
    dev: bool = False

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

    # ---- Immutable external snapshot ----
    snapshot_path: str = _DEFAULT_SNAPSHOT_PATH
    data_bundle_path: str = ""
    data_bundle_sha256: str = ""
    data_expanded_sha256: str = ""
    # Single-sourced with the ETL's stamp: a bundle built under a different contract is
    # refused at materialization rather than served (see clingen_link.data_contract).
    data_schema_version: str = SNAPSHOT_SCHEMA_SEMVER
    data_schema_minimum: str = SNAPSHOT_SCHEMA_SEMVER
    data_schema_maximum: str = SNAPSHOT_SCHEMA_SEMVER
    data_max_compressed_bytes: int = 64 * 1024 * 1024
    data_max_expanded_bytes: int = 256 * 1024 * 1024
    data_root: str = _DEFAULT_DATA_ROOT
    data_release_tag: str = _DEFAULT_DATA_RELEASE_TAG
    data_identity_digest: str = _DEFAULT_DATA_IDENTITY_DIGEST

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
    # Streamable HTTP only: ``unified`` (FastAPI host + mounted MCP HTTP) and the
    # ``http`` alias. No stdio.
    MCP_TRANSPORT: Literal["unified", "http"] = "unified"
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8000
    MCP_PATH: str = "/mcp"
    ALLOWED_HOSTS: list[str] = ["localhost", "127.0.0.1", "::1"]
    ALLOWED_ORIGINS: list[str] = []

    # ---- Logging ----
    LOG_LEVEL: str = "INFO"
    MCP_LOG_LEVEL: str = "INFO"
    # Renderer: ``json`` (production, structured) or ``console`` (human-friendly
    # dev). Defaults to JSON per the GeneFoundry Logging Standard v1.
    LOG_FORMAT: Literal["json", "console"] = "json"

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

    @field_validator("data_release_tag")
    @classmethod
    def _validate_data_release_tag(cls, value: str) -> str:
        if not _RELEASE_TAG.fullmatch(value) or value.lower() in _MUTABLE_RELEASE_TAGS:
            raise ValueError("data release tag must be an immutable release identifier")
        return value

    @field_validator("data_identity_digest")
    @classmethod
    def _validate_data_identity_digest(cls, value: str) -> str:
        if not _SHA256_DIGEST.fullmatch(value):
            raise ValueError("data identity digest must be sha256 plus 64 lowercase hex characters")
        return value

    @field_validator("ALLOWED_HOSTS", "ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_string_list(cls, v: object) -> list[str]:
        """Parse exact allowlists from comma-separated values or JSON lists."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return list(v) if isinstance(v, (list, tuple)) else []

    @field_validator("ALLOWED_HOSTS")
    @classmethod
    def _reject_wildcard_host(cls, v: list[str]) -> list[str]:
        """Require exact hosts; pattern syntax makes the boundary ambiguous."""
        if any(any(marker in host for marker in "*?[]") for host in v):
            raise ValueError("wildcard patterns are not allowed in ALLOWED_HOSTS")
        return v

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

    def data_requirement(self) -> DataRequirement:
        """Return the exact external bundle contract, failing closed if incomplete."""
        if not self.data_bundle_path:
            raise ValueError("CLINGEN_LINK_DATA_BUNDLE_PATH is required")
        return DataRequirement(
            bundle_path=Path(self.data_bundle_path),
            release_tag=self.data_release_tag,
            compressed_sha256=self.data_bundle_sha256,
            expanded_tree_sha256=self.data_expanded_sha256,
            schema_version=self.data_schema_version,
            schema_minimum=self.data_schema_minimum,
            schema_maximum=self.data_schema_maximum,
            max_compressed_bytes=self.data_max_compressed_bytes,
            max_expanded_bytes=self.data_max_expanded_bytes,
        )

    def expected_data_identity(self) -> dict[str, str]:
        """Return the readiness tuple that the selected materialization must expose."""
        requirement = self.data_requirement()
        return {
            "mode": "external-reference",
            "compressed_sha256": requirement.compressed_sha256,
            "expanded_tree_sha256": requirement.expanded_tree_sha256,
            "schema_version": requirement.schema_version,
            "schema_minimum": requirement.schema_minimum,
            "schema_maximum": requirement.schema_maximum,
        }


# Global settings instance
settings = Settings()
