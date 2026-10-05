import pytest
from pydantic import ValidationError

from src.models import FilingMetadata

VALID_KWARGS = dict(
    ticker="AAPL",
    company_name="Apple",
    cik="320193",
    form_type="10-K",
    filing_date="2024-11-01",
    report_date="2024-09-28",
    accession_number="0000320193-24-000123",
    primary_document="aapl-20240928.htm",
    document_url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
)


def test_cik_is_zero_padded_to_ten_digits():
    filing = FilingMetadata(**VALID_KWARGS)
    assert filing.cik == "0000320193"


def test_accession_number_compact_strips_dashes():
    filing = FilingMetadata(**VALID_KWARGS)
    assert filing.accession_number_compact == "000032019324000123"


def test_rejects_non_numeric_cik():
    with pytest.raises(ValidationError):
        FilingMetadata(**{**VALID_KWARGS, "cik": "not-a-cik"})


def test_rejects_malformed_accession_number():
    with pytest.raises(ValidationError):
        FilingMetadata(**{**VALID_KWARGS, "accession_number": "12345"})


def test_rejects_non_10k_form_type():
    with pytest.raises(ValidationError):
        FilingMetadata(**{**VALID_KWARGS, "form_type": "10-Q"})


def test_accepts_10k_amendment():
    filing = FilingMetadata(**{**VALID_KWARGS, "form_type": "10-K/A"})
    assert filing.form_type == "10-K/A"
