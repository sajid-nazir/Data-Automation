import pytest

from src.resolver import resolve_latest_10k


@pytest.fixture
def submissions_payload() -> dict:
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
            }
        }
    }


def test_resolves_first_standard_10k_skipping_amendments(submissions_payload: dict):
    filing = resolve_latest_10k(
        submissions_payload,
        ticker="AAPL",
        company_name="Apple",
        cik="0000320193",
    )

    assert filing is not None
    assert filing.form_type == "10-K"
    assert filing.accession_number == "0000320193-24-000123"
    assert filing.primary_document == "aapl-20240928.htm"
    assert filing.filing_date.isoformat() == "2024-10-01"
    assert filing.report_date.isoformat() == "2024-09-28"


def test_builds_archive_url_with_unpadded_cik_and_unhyphenated_accession(
    submissions_payload: dict,
):
    filing = resolve_latest_10k(
        submissions_payload,
        ticker="AAPL",
        company_name="Apple",
        cik="0000320193",
    )

    assert filing is not None
    assert str(filing.document_url) == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019324000123/aapl-20240928.htm"
    )


def test_passes_through_ticker_and_company_name(submissions_payload: dict):
    filing = resolve_latest_10k(
        submissions_payload,
        ticker="AAPL",
        company_name="Apple",
        cik="0000320193",
    )

    assert filing is not None
    assert filing.ticker == "AAPL"
    assert filing.company_name == "Apple"
    assert filing.cik == "0000320193"


def test_returns_none_when_no_standard_10k_present():
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
            }
        }
    }

    filing = resolve_latest_10k(payload, ticker="AAPL", company_name="Apple", cik="0000320193")

    assert filing is None


def test_returns_none_when_recent_filings_are_empty():
    payload = {"filings": {"recent": {}}}

    filing = resolve_latest_10k(payload, ticker="AAPL", company_name="Apple", cik="0000320193")

    assert filing is None
