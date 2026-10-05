"""HTTP client for SEC EDGAR: compliant headers, retry/backoff, and a 10 req/s rate limiter."""

from __future__ import annotations

import logging
import time
from typing import Any, cast

import httpx

from src.config import Settings, settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 503}


class SECRequestError(RuntimeError):
    """Raised when a SEC EDGAR request fails after exhausting all retry attempts."""


class SECClient:
    """Rate-limited, retrying HTTP client for the SEC submissions API."""

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
        return self._get_json(url)

    def _get_json(self, url: str) -> dict[str, Any]:
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
                    return cast(dict[str, Any], response.json())
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
                backoff = self._settings.backoff_base_seconds * (2 ** (attempt - 1))
                time.sleep(backoff)

        raise SECRequestError(
            f"Failed to fetch {url} after {self._settings.max_retries} attempt(s)"
        ) from last_error

    def close(self) -> None:
        self._client.close()
