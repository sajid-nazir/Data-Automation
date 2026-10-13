"""HTTP client for SEC EDGAR: compliant headers, retry/backoff, host allowlist, and a
rate limiter."""

from __future__ import annotations

import email.utils
import logging
import math
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, cast

import httpx

from sec_filings.config import Settings
from sec_filings.errors import (
    DisallowedHostError,
    SECBlockedError,
    SECRequestError,
    SECResponseError,
)

logger = logging.getLogger(__name__)

# 429/503 are SEC's own rate-limit signals; 500/502/504 are transient upstream/edge
# errors worth a retry. 403/404 and other 4xx are not retried: they won't resolve
# on their own and retrying them just burns the request budget.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# A literal fragment of SEC's "Undeclared Automated Tool" block page. Checked against
# every HTML response regardless of status code: the real block was observed arriving
# as a 403, not the 200 an earlier version of this client assumed.
_BLOCK_PAGE_MARKER = "Your Request Originates from an Undeclared Automated Tool"

_SUBMISSIONS_PAGE_NAME = re.compile(r"^CIK\d{10}-submissions-\d{3}\.json$")

# Transport-level failures worth retrying: timeouts and connection drops are
# transient. Everything else (UnsupportedProtocol, InvalidURL, ...) is a
# deterministic error that retrying can't fix.
_RETRYABLE_TRANSPORT_ERRORS = (
    httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError,
)


class SECClient:
    """Rate-limited, retrying HTTP client for the SEC submissions API and archive documents.

    All requests to sec.gov (submissions JSON, filing documents, embedded images alike)
    go through this client, so every call is subject to the same throttle, User-Agent,
    host allowlist, and retry/backoff policy - including redirect hops, which are
    checked against the allowlist individually via an httpx request hook.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._min_interval = 1.0 / settings.rate_limit_per_second
        self._last_request_time = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            event_hooks={"request": [self._check_host]},
        )

    def __enter__(self) -> SECClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _check_host(self, request: httpx.Request) -> None:
        """httpx request hook: fires for the initial request and every redirect hop."""
        hostname = request.url.host
        if hostname not in self._settings.allowed_hosts:
            raise DisallowedHostError(
                f"Refusing to request disallowed host {hostname!r}",
                url=str(request.url),
            )

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()

    def get_submissions(self, cik: str) -> dict[str, Any]:
        """Fetch the raw submissions JSON for a given (zero-padded) CIK."""
        url = f"{self._settings.submissions_base_url}/CIK{cik.zfill(10)}.json"
        return self._get_json(url)

    def get_submissions_page(self, name: str) -> dict[str, Any]:
        """Fetch an overflow page listed in the main submissions JSON's filings.files[]."""
        if not _SUBMISSIONS_PAGE_NAME.match(name):
            raise SECResponseError(
                f"Refusing to fetch submissions page with unexpected name {name!r}"
            )
        url = f"{self._settings.submissions_base_url}/{name}"
        return self._get_json(url)

    def _get_json(self, url: str) -> dict[str, Any]:
        response = self._request(url)
        try:
            data = response.json()
        except ValueError as exc:
            raise SECResponseError(f"SEC returned non-JSON body for {url}") from exc
        if not isinstance(data, dict):
            raise SECResponseError(
                f"SEC returned unexpected JSON shape for {url}: {type(data).__name__}"
            )
        return cast(dict[str, Any], data)

    def get_document(self, url: str) -> str:
        """Fetch a filing document's raw HTML (or other text) from the SEC archive."""
        return self._request(url).text

    def get_binary(self, url: str) -> tuple[bytes, str]:
        """Fetch raw bytes (e.g. an image embedded in a filing) and its content type."""
        response = self._request(url)
        content_type = response.headers.get("content-type", "application/octet-stream")
        return response.content, content_type

    def _is_block_page(self, response: httpx.Response) -> bool:
        content_type = response.headers.get("content-type", "")
        return "html" in content_type and _BLOCK_PAGE_MARKER in response.text

    def _request(self, url: str) -> httpx.Response:
        deadline = time.monotonic() + self._settings.request_deadline_seconds
        last_error: Exception | None = None
        last_status_code: int | None = None
        attempt = 0

        for attempt in range(1, self._settings.max_attempts + 1):
            self._throttle()
            try:
                response = self._client.get(url)
            except _RETRYABLE_TRANSPORT_ERRORS as exc:
                last_error = exc
                logger.warning(
                    "Transport error fetching %s (attempt %d/%d): %s",
                    url, attempt, self._settings.max_attempts, exc,
                )
            except httpx.HTTPError as exc:
                raise SECRequestError(
                    f"Non-retryable error fetching {url}: {exc}", url=url
                ) from exc
            else:
                if self._is_block_page(response):
                    raise SECBlockedError(
                        f"SEC served its automated-traffic block page for {url}",
                        url=url,
                        status_code=response.status_code,
                    )
                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    if response.is_success:
                        return response
                    raise SECRequestError(
                        f"SEC returned {response.status_code} for {url}",
                        url=url,
                        status_code=response.status_code,
                    )
                last_status_code = response.status_code
                last_error = httpx.HTTPStatusError(
                    f"SEC returned {response.status_code} for {url}",
                    request=response.request,
                    response=response,
                )
                logger.warning(
                    "SEC returned %d for %s (attempt %d/%d)",
                    response.status_code, url, attempt, self._settings.max_attempts,
                )

            if attempt == self._settings.max_attempts:
                break
            delay = self._backoff_seconds(attempt, last_error)
            if time.monotonic() + delay > deadline:
                logger.warning(
                    "Backoff would exceed the request deadline for %s; giving up", url
                )
                break
            time.sleep(delay)

        raise SECRequestError(
            f"Failed to fetch {url} after {attempt} attempt(s)",
            url=url,
            status_code=last_status_code,
        ) from last_error

    def _backoff_seconds(self, attempt: int, error: Exception | None) -> float:
        base: float = min(
            float(self._settings.max_backoff_seconds),
            float(self._settings.backoff_base_seconds) * (2 ** (attempt - 1)),
        )
        retry_after = self._parse_retry_after(error)
        if retry_after is not None:
            base = max(base, min(retry_after, self._settings.max_backoff_seconds))
        # Full jitter: spreads out retries from multiple callers hitting the same
        # transient failure at the same time, instead of all sleeping in lockstep.
        return base * random.uniform(0.5, 1.0)

    def _parse_retry_after(self, error: Exception | None) -> float | None:
        if not isinstance(error, httpx.HTTPStatusError):
            return None
        raw = error.response.headers.get("Retry-After")
        if raw is None:
            return None

        try:
            value = float(raw)
        except ValueError:
            parsed = email.utils.parsedate_to_datetime(raw)
            if parsed is None:
                return None
            delta = (parsed - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, delta)

        if not math.isfinite(value) or value < 0:
            return None
        return value

    def close(self) -> None:
        self._client.close()
