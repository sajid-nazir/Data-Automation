"""Central configuration: SEC endpoints, compliant User-Agent, and the target company registry."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    """Runtime configuration, overridable via SEC_-prefixed environment variables."""

    model_config = SettingsConfigDict(env_prefix="SEC_", case_sensitive=False)

    # SEC's Fair Access policy requires a descriptive User-Agent with a real
    # contact address. Override with SEC_USER_AGENT in production rather than
    # relying on this fallback.
    user_agent: str = "SEC 10-K Fetcher sajidchnazir@gmail.com"

    submissions_base_url: str = "https://data.sec.gov/submissions"
    archive_base_url: str = "https://www.sec.gov/Archives/edgar/data"

    request_timeout_seconds: float = 15.0
    # SEC's documented ceiling is 10 req/s; this sits at that limit, so client.py
    # must enforce it strictly rather than treat it as a soft target.
    rate_limit_per_second: float = 10.0
    max_retries: int = 5
    backoff_base_seconds: float = 0.5

    output_dir: Path = Path("./output")
    pdf_timeout_ms: int = 60_000


settings = Settings()
