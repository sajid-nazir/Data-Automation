"""HTTP client for SEC EDGAR: compliant headers, retry/backoff, and a 10 req/s rate limiter."""

from __future__ import annotations

import logging
import time
from typing import Any, cast

import httpx

from sec_filings.config import Settings, settings

logger = logging.getLogger(__name__)

# 429/503 are SEC's own rate-limit signals; 500/502/504 are transient upstream/edge
# errors worth a retry. 403/404 and other 4xx are not retried: they won't resolve
# on their own and retrying them just burns the request budget.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# A literal fragment of SEC's "Undeclared Automated Tool" block page. If this ever
# shows up in a response body, the request wasn't actually denied (no 4xx/5xx) but
# the content isn't the document we asked for either - worth failing loudly instead
# of silently saving the block page as if it were a filing.
_BLOCK_PAGE_MARKER = "Your Request Originates from an Undeclared Automated Tool"


class SECRequestError(RuntimeError):
    """Raised when a SEC EDGAR request fails after exhausting all retry attempts."""


class SECBlockedError(SECRequestError):
    """Raised when SEC serves its automated-traffic block page instead of the document."""


class SECClient:
    """Rate-limited, retrying HTTP client for the SEC submissions API and archive documents.

    All requests to sec.gov (submissions JSON and archived filing documents alike) go
    through this client, so every call is subject to the same throttle, User-Agent, and
    retry/backoff policy.
    """

    def __init__(self, settings: Settings = settings) -> None:
        self._settings = settings
        self._min_interval = 1.0 / settings.rate_limit_per_second
        self._last_request_time = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout_seconds,
        )

    def __enter__(self) -> "SECClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()

    def get_submissions(self, cik: str) -> dict[str, Any]:
        """Fetch the raw submissions JSON for a given (zero-padded) CIK."""
        url = f"{self._settings.submissions_base_url}/CIK{cik.zfill(10)}.json"
        return cast(dict[str, Any], self._request(url).json())

    def get_document(self, url: str) -> str:
        """Fetch a filing document's raw HTML (or other text) from the SEC archive."""
        text = self._request(url).text
        if _BLOCK_PAGE_MARKER in text:
            raise SECBlockedError(
                f"SEC served its automated-traffic block page for {url} "
                "instead of the requested document"
            )
        return text

    def get_binary(self, url: str) -> bytes:
        """Fetch raw bytes (e.g. an image embedded in a filing) from the SEC archive."""
        return self._request(url).content

    def _request(self, url: str) -> httpx.Response:
        last_error: Exception | None = None

        for attempt in range(1, self._settings.max_retries + 1):
            self._throttle()
            try:
                response = self._client.get(url)
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning(
                    "Transport error fetching %s (attempt %d/%d): %s",
                    url, attempt, self._settings.max_retries, exc,
                )
            else:
                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    response.raise_for_status()
                    return response
                last_error = httpx.HTTPStatusError(
                    f"SEC returned {response.status_code} for {url}",
                    request=response.request,
                    response=response,
                )
                logger.warning(
                    "SEC returned %d for %s (attempt %d/%d)",
                    response.status_code, url, attempt, self._settings.max_retries,
                )

            if attempt < self._settings.max_retries:
                time.sleep(self._backoff_seconds(attempt, last_error))

        raise SECRequestError(
            f"Failed to fetch {url} after {self._settings.max_retries} attempt(s)"
        ) from last_error

    def _backoff_seconds(self, attempt: int, error: Exception | None) -> float:
        """Honor a server-supplied Retry-After if present and larger than our own backoff."""
        default: float = float(self._settings.backoff_base_seconds) * (2 ** (attempt - 1))
        if isinstance(error, httpx.HTTPStatusError):
            retry_after = error.response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    return max(default, float(retry_after))
                except ValueError:
                    pass
        return default

    def close(self) -> None:
        self._client.close()
