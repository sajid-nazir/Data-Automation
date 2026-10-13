import pytest

from sec_filings.config import Settings


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    """Settings tuned for fast, deterministic tests: no real sleeping, small output dir."""
    return Settings(
        user_agent="Test Suite test@example.com",
        rate_limit_per_second=10.0,  # the configured ceiling; keeps tests fast without exceeding it
        max_attempts=3,
        backoff_base_seconds=0.001,
        max_backoff_seconds=0.01,
        request_deadline_seconds=5.0,
        request_timeout_seconds=5.0,
        output_dir=tmp_path,
        # Large by default so ordinary fixture dates (which don't track the real
        # calendar) never accidentally trip staleness; tests that specifically
        # exercise StaleFilingError override this back down explicitly.
        max_filing_age_days=100_000,
    )
