from datetime import date
from typing import Any

import pytest

from sec_filings.config import Settings
from sec_filings.errors import FilingNotFoundError, SECResponseError, StaleFilingError
from sec_filings.resolver import resolve_latest_10k

_EMPTY_COLUMNS: dict[str, list[str]] = {
    "form": [], "accessionNumber": [], "filingDate": [], "reportDate": [], "primaryDocument": [],
}


@pytest.fixture
def submissions_payload() -> dict[str, Any]:
    """Mimics filings.recent's parallel-array shape, newest filing first.

    Includes a newer 10-K/A ahead of the standard 10-K to verify amendments
    are skipped in favor of the first exact "10-K" match.
    """
    return {
        "filings": {
            "recent": {
                "form": ["4", "10-Q", "10-K/A", "10-K", "8-K"],
                "accessionNumber": [
                    "0000320193-24-000200",
                    "0000320193-24-000150",
                    "0000320193-24-000130",
                    "0000320193-24-000123",
                    "0000320193-24-000100",
                ],
                "filingDate": [
                    "2024-11-15",
                    "2024-11-01",
                    "2024-10-15",
                    "2024-10-01",
                    "2024-09-01",
                ],
                "reportDate": [
                    "2024-11-01",
                    "2024-09-28",
                    "2024-06-29",
                    "2024-09-28",
                    "2024-08-30",
                ],
                "primaryDocument": [
                    "form4.xml",
                    "aapl-10q.htm",
                    "aapl-10ka.htm",
                    "aapl-20240928.htm",
                    "aapl-8k.htm",
                ],
            },
            "files": [],
        }
    }


@pytest.fixture
def recent_today() -> date:
    return date(2024, 11, 20)


def test_resolves_first_standard_10k_skipping_amendments(
    submissions_payload: dict[str, Any], test_settings: Settings, recent_today: date
):
    filing = resolve_latest_10k(
        submissions_payload,
        ticker="AAPL",
        company_name="Apple",
        cik="0000320193",
        settings=test_settings,
        today=recent_today,
    )

    assert filing.form_type == "10-K"
    assert filing.accession_number == "0000320193-24-000123"
    assert filing.primary_document == "aapl-20240928.htm"
    assert filing.filing_date.isoformat() == "2024-10-01"
    assert filing.report_date.isoformat() == "2024-09-28"


def test_picks_latest_filing_date_among_multiple_10ks(test_settings: Settings, recent_today: date):
    payload = {
        "filings": {
            "recent": {
                "form": ["10-K", "10-K"],
                "accessionNumber": ["0000320193-23-000050", "0000320193-24-000123"],
                "filingDate": ["2023-10-01", "2024-10-01"],
                "reportDate": ["2023-09-30", "2024-09-28"],
                "primaryDocument": ["old.htm", "new.htm"],
            },
            "files": [],
        }
    }
    filing = resolve_latest_10k(
        payload, ticker="AAPL", company_name="Apple", cik="0000320193",
        settings=test_settings, today=recent_today,
    )
    assert filing.primary_document == "new.htm"


def test_builds_archive_url_with_unpadded_cik_and_unhyphenated_accession(
    submissions_payload: dict[str, Any], test_settings: Settings, recent_today: date
):
    filing = resolve_latest_10k(
        submissions_payload, ticker="AAPL", company_name="Apple", cik="0000320193",
        settings=test_settings, today=recent_today,
    )
    assert str(filing.document_url) == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019324000123/aapl-20240928.htm"
    )


def test_passes_through_ticker_and_company_name(
    submissions_payload: dict[str, Any], test_settings: Settings, recent_today: date
):
    filing = resolve_latest_10k(
        submissions_payload, ticker="AAPL", company_name="Apple", cik="0000320193",
        settings=test_settings, today=recent_today,
    )
    assert filing.ticker == "AAPL"
    assert filing.company_name == "Apple"
    assert filing.cik == "0000320193"


def test_raises_filing_not_found_when_no_standard_10k_and_no_fallback(test_settings: Settings):
    payload = {
        "filings": {
            "recent": {
                "form": ["10-K/A", "10-Q", "8-K"],
                "accessionNumber": [
                    "0000320193-24-000130",
                    "0000320193-24-000150",
                    "0000320193-24-000100",
                ],
                "filingDate": ["2024-10-15", "2024-11-01", "2024-09-01"],
                "reportDate": ["2024-06-29", "2024-09-28", "2024-08-30"],
                "primaryDocument": ["aapl-10ka.htm", "aapl-10q.htm", "aapl-8k.htm"],
            },
            "files": [],
        }
    }

    with pytest.raises(FilingNotFoundError):
        resolve_latest_10k(
            payload, ticker="AAPL", company_name="Apple", cik="0000320193",
            settings=test_settings,
        )


def test_raises_filing_not_found_when_recent_is_empty(test_settings: Settings):
    payload: dict[str, Any] = {"filings": {"recent": {}, "files": []}}

    with pytest.raises(FilingNotFoundError):
        resolve_latest_10k(
            payload, ticker="AAPL", company_name="Apple", cik="0000320193",
            settings=test_settings,
        )


def test_raises_response_error_on_mismatched_column_lengths(test_settings: Settings):
    payload = {
        "filings": {
            "recent": {
                "form": ["10-K", "10-Q"],
                "accessionNumber": ["0000320193-24-000123"],  # ragged: only 1 entry
                "filingDate": ["2024-10-01", "2024-11-01"],
                "reportDate": ["2024-09-28", "2024-09-28"],
                "primaryDocument": ["a.htm", "b.htm"],
            },
            "files": [],
        }
    }

    with pytest.raises(SECResponseError):
        resolve_latest_10k(
            payload, ticker="AAPL", company_name="Apple", cik="0000320193",
            settings=test_settings,
        )


def test_raises_stale_filing_error_beyond_max_age(test_settings: Settings):
    payload = {
        "filings": {
            "recent": {
                "form": ["10-K"],
                "accessionNumber": ["0000320193-20-000123"],
                "filingDate": ["2020-01-01"],
                "reportDate": ["2019-12-31"],
                "primaryDocument": ["old.htm"],
            },
            "files": [],
        }
    }

    tight_settings = test_settings.model_copy(update={"max_filing_age_days": 550})
    with pytest.raises(StaleFilingError):
        resolve_latest_10k(
            payload, ticker="AAPL", company_name="Apple", cik="0000320193",
            settings=tight_settings, today=date(2024, 11, 20),
        )


# --- pagination fallback ---------------------------------------------------------


def test_falls_back_to_overflow_page_when_recent_has_no_10k(
    test_settings: Settings, recent_today: date
):
    payload = {
        "filings": {
            "recent": {
                "form": ["10-Q"],
                "accessionNumber": ["0000886982-24-000150"],
                "filingDate": ["2024-11-01"],
                "reportDate": ["2024-09-28"],
                "primaryDocument": ["gs-10q.htm"],
            },
            "files": [
                {"name": "CIK0000886982-submissions-001.json", "filingTo": "2023-12-31"},
            ],
        }
    }
    overflow_page = {
        "form": ["10-K"],
        "accessionNumber": ["0000886982-23-000091"],
        "filingDate": ["2023-02-25"],
        "reportDate": ["2022-12-31"],
        "primaryDocument": ["gs-10k.htm"],
    }

    def fetch_page(name: str) -> dict[str, Any]:
        assert name == "CIK0000886982-submissions-001.json"
        return overflow_page

    filing = resolve_latest_10k(
        payload, ticker="GS", company_name="Goldman Sachs", cik="0000886982",
        settings=test_settings, fetch_page=fetch_page, today=date(2023, 6, 1),
    )
    assert filing.primary_document == "gs-10k.htm"


def test_pagination_checks_pages_newest_first(test_settings: Settings):
    payload = {
        "filings": {
            "recent": _EMPTY_COLUMNS,
            "files": [
                {"name": "CIK0000886982-submissions-001.json", "filingTo": "2022-12-31"},
                {"name": "CIK0000886982-submissions-002.json", "filingTo": "2023-12-31"},
            ],
        }
    }
    fetched_order: list[str] = []

    def fetch_page(name: str) -> dict[str, Any]:
        fetched_order.append(name)
        if name == "CIK0000886982-submissions-002.json":
            return {
                "form": ["10-K"],
                "accessionNumber": ["0000886982-23-000091"],
                "filingDate": ["2023-02-25"],
                "reportDate": ["2022-12-31"],
                "primaryDocument": ["gs-10k.htm"],
            }
        return _EMPTY_COLUMNS

    resolve_latest_10k(
        payload, ticker="GS", company_name="Goldman Sachs", cik="0000886982",
        settings=test_settings, fetch_page=fetch_page, today=date(2023, 6, 1),
    )
    assert fetched_order[0] == "CIK0000886982-submissions-002.json"


def test_raises_filing_not_found_when_no_10k_in_any_overflow_page(test_settings: Settings):
    payload = {
        "filings": {
            "recent": _EMPTY_COLUMNS,
            "files": [{"name": "CIK0000886982-submissions-001.json", "filingTo": "2022-12-31"}],
        }
    }

    def fetch_page(name: str) -> dict[str, Any]:
        return {
            "form": ["10-Q"], "accessionNumber": ["x"], "filingDate": ["2022-01-01"],
            "reportDate": ["2021-12-31"], "primaryDocument": ["x.htm"],
        }

    with pytest.raises(FilingNotFoundError):
        resolve_latest_10k(
            payload, ticker="GS", company_name="Goldman Sachs", cik="0000886982",
            settings=test_settings, fetch_page=fetch_page,
        )


def test_no_fallback_attempted_when_fetch_page_not_given(test_settings: Settings):
    payload = {
        "filings": {
            "recent": _EMPTY_COLUMNS,
            "files": [{"name": "CIK0000886982-submissions-001.json", "filingTo": "2022-12-31"}],
        }
    }

    with pytest.raises(FilingNotFoundError):
        resolve_latest_10k(
            payload, ticker="GS", company_name="Goldman Sachs", cik="0000886982",
            settings=test_settings,
        )
