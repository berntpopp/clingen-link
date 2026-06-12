"""Custom exceptions for the clingen-link server.

Two families live here:

* Server-lifecycle exceptions (`ConfigurationError`, `StartupError`,
  `MCPIntegrationError`, ...) mirror the gnomad-link house style and drive the
  transport/startup error handling.
* ClinGen data/upstream exceptions (`ClingenApiError` and friends, plus
  `SnapshotUnavailableError`) are the typed faults the MCP error boundary
  (`clingen_link.mcp.errors._classify`) maps to envelope `error_code`s. The
  live HTTP client (Phase 3) and the read-only store (Phase 3) raise these; they
  are defined here in Phase 1 so the envelope taxonomy is stable from the start.
"""

from __future__ import annotations


class ClingenServerError(Exception):
    """Base exception for clingen-link server errors."""

    def __init__(self, message: str, transport: str | None = None) -> None:
        """Initialize a server error with a message and optional transport context."""
        super().__init__(message)
        self.transport = transport


class TransportError(ClingenServerError):
    """Exception for transport-related errors."""


class ConfigurationError(ClingenServerError):
    """Exception for configuration validation errors."""


class StartupError(ClingenServerError):
    """Exception for server startup errors."""


class ShutdownError(ClingenServerError):
    """Exception for server shutdown errors."""


class MCPIntegrationError(TransportError):
    """Exception for MCP integration errors."""


class HTTPTransportError(TransportError):
    """Exception for HTTP transport errors."""


class STDIOTransportError(TransportError):
    """Exception for STDIO transport errors."""


# ---------------------------------------------------------------------------
# ClinGen data / upstream fault taxonomy
# ---------------------------------------------------------------------------
#
# These map deterministically to MCP envelope error codes in
# clingen_link.mcp.errors._classify(). Subclass ordering matters there: the
# specific subclasses below MUST be checked before the generic ClingenApiError.


class ClingenApiError(Exception):
    """Base exception for a failed ClinGen upstream interaction.

    Maps to ``upstream_unavailable`` (retryable) unless a more specific subclass
    applies.
    """


class DataNotFoundError(ClingenApiError):
    """A well-formed identifier or query that the upstream/snapshot has no data for.

    Maps to ``not_found`` (not retryable).
    """


class UpstreamInputError(ClingenApiError):
    """A deterministic upstream rejection (wrong identifier shape, bad parameters).

    Maps to ``invalid_input`` (not retryable): retrying unchanged can never
    succeed.
    """


class RateLimitedError(ClingenApiError):
    """Upstream rate limiting (HTTP 429) or local concurrency saturation.

    Maps to ``rate_limited`` (retryable after backoff).
    """


class SnapshotUnavailableError(ClingenServerError):
    """The bundled SQLite snapshot is missing, unreadable, or not yet built.

    Maps to ``snapshot_unavailable`` (not retryable by the caller; the operator
    must run ``clingen-link refresh``). Surfaced via diagnostics.
    """
