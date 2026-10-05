"""Resolves the latest 10-K filing metadata from EDGAR submissions JSON columnar arrays."""

from __future__ import annotations

from typing import Any

from pydantic import HttpUrl

from src.config import Settings, settings
from src.models import FilingMetadata


def resolve_latest_10k(
    submissions: dict[str, Any],
    *,
    ticker: str,
    company_name: str,
    cik: str,
    settings: Settings = settings,
) -> FilingMetadata | None:
    """Return the most recent standard 10-K filing's metadata, or None if none is found.

    Amendments (10-K/A) are skipped; only an exact "10-K" form match is returned.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession_numbers = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_documents = recent.get("primaryDocument", [])

    for index, form in enumerate(forms):
        if form != "10-K":
            continue

        accession_number = accession_numbers[index]
        primary_document = primary_documents[index]
        cik_unpadded = str(int(cik))
        accession_compact = accession_number.replace("-", "")

        document_url = HttpUrl(
            f"{settings.archive_base_url}/{cik_unpadded}/"
            f"{accession_compact}/{primary_document}"
        )

        return FilingMetadata(
            ticker=ticker,
            company_name=company_name,
            cik=cik,
            form_type=form,
            filing_date=filing_dates[index],
            report_date=report_dates[index],
            accession_number=accession_number,
            primary_document=primary_document,
            document_url=document_url,
        )

    return None
