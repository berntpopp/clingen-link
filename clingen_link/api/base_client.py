"""Async httpx resilience layer for live ClinGen drill-down.

Mirrors the gnomad-link base-client discipline over ``httpx.AsyncClient``:

* a single shared ``AsyncClient`` per instance (HTTP/1.1 keep-alive pooling),
  lifecycle-managed via :meth:`aclose` / async context manager;
* an ``asyncio.Semaphore`` bounding in-flight upstream requests so a fan-out of
  tool calls cannot stampede the upstream rate limiter;
* a bounded **queue wait** for a concurrency slot → a fast, retryable
  :class:`RateLimitedError` instead of hanging on the caller's own timeout;
* jittered exponential backoff retrying only transient faults — HTTP
  ``{429, 500, 502, 503, 504}`` and transport/timeout errors;
* deterministic fault classification: 404 → :class:`DataNotFoundError`, other
  4xx → :class:`UpstreamInputError`, a surviving 429 → :class:`RateLimitedError`,
  everything else → :class:`ClingenApiError`.

The fault taxonomy maps 1:1 onto the MCP envelope error codes in
``clingen_link.mcp.errors._classify``.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from ..config import settings
from ..exceptions import (
    ClingenApiError,
    DataNotFoundError,
    RateLimitedError,
    UpstreamInputError,
)

logger = logging.getLogger(__name__)

# Transient HTTP statuses worth retrying (rate limit + transient upstream faults).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Jittered exponential backoff parameters for the retry layer.
_MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 20.0


class BaseClient:
    """Resilient async HTTP client for ClinGen live endpoints."""

    def __init__(
        self,
        *,
        max_concurrency: int | None = None,
        timeout_s: float | None = None,
        queue_wait_timeout_s: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Build the client; ``client`` may be injected (tests use respx mounts).

        Args:
            max_concurrency: in-flight request cap (defaults to settings).
            timeout_s: per-request upstream timeout (defaults to settings).
            queue_wait_timeout_s: max seconds to wait for a concurrency slot.
            client: an existing ``AsyncClient`` to share (else one is built).
        """
        self._timeout = settings.request_timeout_s if timeout_s is None else timeout_s
        limit = settings.max_concurrency if max_concurrency is None else max_concurrency
        self._queue_wait = (
            settings.queue_wait_timeout_s if queue_wait_timeout_s is None else queue_wait_timeout_s
        )
        self._semaphore = asyncio.Semaphore(max(1, limit))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    # ------------------------------------------------------------------
    # Concurrency control
    # ------------------------------------------------------------------
    async def _acquire_slot(self, *, timeout: float) -> None:
        """Acquire a concurrency slot, bounding the wait for fast backpressure.

        An aggressive fan-out otherwise queues every excess request on the
        semaphore until the caller's own tool-call timeout fires, surfacing as an
        opaque hang. A bounded wait raises a retryable :class:`RateLimitedError`
        instead so the LLM can back off and fan out fewer calls.
        """
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=max(0.0, timeout))
        except TimeoutError as exc:
            raise RateLimitedError(
                f"Local concurrency limit saturated (max {settings.max_concurrency} "
                "concurrent upstream requests). Retry with exponential backoff or fan "
                "out fewer calls at once."
            ) from exc

    # ------------------------------------------------------------------
    # Request execution
    # ------------------------------------------------------------------
    async def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        """GET ``url`` and return parsed JSON, with retry + fault classification.

        Raises:
            DataNotFoundError: upstream returned 404.
            UpstreamInputError: upstream returned a non-404 4xx (deterministic).
            RateLimitedError: a 429 survived retries or the slot queue saturated.
            ClingenApiError: any other upstream/transport failure.
        """
        response = await self._request_with_retry(url, params)
        try:
            return response.json()
        except ValueError as exc:  # malformed JSON body
            raise ClingenApiError(f"Upstream returned non-JSON body from {url}: {exc}") from exc

    async def _request_with_retry(self, url: str, params: dict[str, Any] | None) -> httpx.Response:
        """Run one GET through bounded concurrency + jittered retry.

        Only transient faults (retryable statuses + transport/timeout errors)
        retry; a deterministic 4xx propagates immediately. A saturated slot queue
        raises :class:`RateLimitedError` (not retried here — fast backpressure).
        """
        delay = _BACKOFF_BASE_SECONDS
        loop = asyncio.get_running_loop()
        queue_deadline = loop.time() + self._queue_wait
        last_exc: BaseException | None = None
        for attempt in range(_MAX_ATTEMPTS):
            remaining = max(0.0, queue_deadline - loop.time())
            await self._acquire_slot(timeout=remaining)
            try:
                response = await self._client.get(url, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt == _MAX_ATTEMPTS - 1:
                    raise ClingenApiError(f"Upstream transport error for {url}: {exc}") from exc
                await self._backoff(delay)
                delay = min(delay * 2, _BACKOFF_MAX_SECONDS)
                continue
            finally:
                self._semaphore.release()

            if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
                await self._backoff(delay)
                delay = min(delay * 2, _BACKOFF_MAX_SECONDS)
                continue
            return self._classify_response(response, url)
        # Loop exhausted on transient transport faults.
        raise ClingenApiError(  # pragma: no cover - guarded by raise above
            f"Upstream request to {url} failed after {_MAX_ATTEMPTS} attempts: {last_exc}"
        )

    @staticmethod
    async def _backoff(delay: float) -> None:
        """Sleep a full-jittered backoff interval (de-synchronises bursts)."""
        await asyncio.sleep(random.uniform(0, min(delay, _BACKOFF_MAX_SECONDS)))  # noqa: S311

    @staticmethod
    def _classify_response(response: httpx.Response, url: str) -> httpx.Response:
        """Map a final HTTP status to a typed fault, or return the response."""
        code = response.status_code
        if code < 400:
            return response
        if code == 404:
            raise DataNotFoundError(f"Not found upstream ({url}).")
        if code == 429:
            raise RateLimitedError(f"Rate limited by upstream (HTTP 429): {url}")
        if 400 <= code < 500:
            raise UpstreamInputError(
                f"Upstream rejected the request as malformed (HTTP {code}): {url}"
            )
        raise ClingenApiError(f"Upstream error (HTTP {code}): {url}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def aclose(self) -> None:
        """Close the underlying client if this instance owns it (idempotent)."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> BaseClient:
        """Async context-manager entry returns self."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Async context-manager exit closes the client."""
        await self.aclose()
