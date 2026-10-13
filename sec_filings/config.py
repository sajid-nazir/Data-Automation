"""Central configuration: SEC endpoints, compliant User-Agent, and the target company registry."""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import TypedDict

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_EMAIL_PATTERN = re.compile(r"\S+@\S+\.\S+")


class CompanyConfig(TypedDict):
    name: str
    cik: str


# SEC requires 10-digit zero-padded CIK strings for the submissions API.
TARGET_COMPANIES: dict[str, CompanyConfig] = {
    "AAPL": {"name": "Apple", "cik": "0000320193"},
    "META": {"name": "Meta", "cik": "0001326801"},
    "GOOGL": {"name": "Alphabet", "cik": "0001652044"},
    "AMZN": {"name": "Amazon", "cik": "0001018724"},
    "NFLX": {"name": "Netflix", "cik": "0001065280"},
    "GS": {"name": "Goldman Sachs", "cik": "0000886982"},
}


class Settings(BaseSettings):
    """Runtime configuration, overridable via SEC_-prefixed environment variables.

    No field has a default that silently identifies someone else to SEC or quietly
    exceeds SEC's published limits - `user_agent` is required, and rate/retry fields
    are constrained to sane ranges at construction time rather than failing lazily
    (e.g. ZeroDivisionError) deep inside client.py.
    """

    model_config = SettingsConfigDict(env_prefix="SEC_", case_sensitive=False)

    # SEC's Fair Access policy requires a descriptive User-Agent with a real contact
    # address. No fallback: a missing or malformed value fails at startup, not with
    # someone else's identity baked in as a quiet default.
    user_agent: str

    submissions_base_url: str = "https://data.sec.gov/submissions"
    archive_base_url: str = "https://www.sec.gov/Archives/edgar/data"

    # Hosts the HTTP client (and the converter's request router) will ever contact.
    # A resolved or redirected URL outside this set is refused, not retried.
    allowed_hosts: frozenset[str] = frozenset({"www.sec.gov", "data.sec.gov"})

    request_timeout_seconds: float = Field(15.0, gt=0)
    # SEC's documented ceiling is 10 req/s. Default sits below it (not at it) for
    # headroom: this is a single-process CLI with no concurrent callers today, but
    # "exactly at the limit" leaves zero margin for clock jitter or a second process
    # sharing the same egress IP.
    rate_limit_per_second: float = Field(8.0, gt=0, le=10.0)
    max_attempts: int = Field(5, ge=1)
    backoff_base_seconds: float = Field(0.5, gt=0)
    max_backoff_seconds: float = Field(60.0, gt=0)
    # Overall ceiling on time spent retrying one request, so a large Retry-After
    # (or a long run of backoffs) can't silently stall the whole batch for ages.
    request_deadline_seconds: float = Field(300.0, gt=0)

    # A resolved "latest" 10-K older than this is treated as a failure, not a
    # silently-returned stale result - e.g. a company that switched filing types.
    max_filing_age_days: int = 550

    output_dir: Path = Path("./output")
    pdf_timeout_ms: int = Field(60_000, gt=0)
    # Tried in order; the first scale that keeps the rendered page count within
    # tolerance of the source document's own page-break markers is used.
    pdf_scales: tuple[float, ...] = (0.9, 0.8, 0.7)

    @field_validator("user_agent")
    @classmethod
    def _require_contact_email(cls, value: str) -> str:
        if not _EMAIL_PATTERN.search(value):
            raise ValueError(
                "SEC_USER_AGENT must include a contact email address, e.g. "
                "'Your Company your-contact@example.com' (SEC's Fair Access policy "
                "requires it, and it's how SEC can reach you if your traffic causes "
                "a problem)"
            )
        return value


@functools.lru_cache
def get_settings() -> Settings:
    """Load settings once per process from the environment."""
    return Settings()  # pydantic-settings fills user_agent from the SEC_USER_AGENT env var
