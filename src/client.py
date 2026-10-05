"""HTTP client for SEC EDGAR: compliant headers, retry/backoff, and a 10 req/s rate limiter.

Scaffold only — the retry/backoff and rate-limiting logic from validate_sec_api.py's
SECClient should be hardened and moved here. Not yet implemented.
"""

from __future__ import annotations

from src.config import Settings, settings


class SECClient:
    """Rate-limited, retrying HTTP client for the SEC submissions and archive endpoints."""

    def __init__(self, settings: Settings = settings) -> None:
        self._settings = settings
        raise NotImplementedError("SECClient is not yet implemented")

    def get_submissions(self, cik: str) -> dict:
        """Fetch the raw submissions JSON for a given (zero-padded) CIK."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
