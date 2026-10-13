"""Resolves the latest 10-K filing metadata from EDGAR submissions JSON columnar arrays."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import HttpUrl, ValidationError

from sec_filings.config import Settings
from sec_filings.errors import FilingNotFoundError, SECResponseError, StaleFilingError
from sec_filings.models import FilingMetadata

_REQUIRED_COLUMNS = ("form", "accessionNumber", "filingDate", "reportDate", "primaryDocument")


def _latest_10k_row(columns: dict[str, Any]) -> dict[str, str] | None:
    """Pick the row with the latest filingDate among exact "10-K" forms.

    `columns` is a columnar dict - `filings.recent` and an overflow page from
    `filings.files[]` share this exact shape, just at different places in the JSON,
    so this one function serves both. filingDate strings are ISO 'YYYY-MM-DD', which
    sorts correctly as plain strings.
    """
    arrays = {name: columns.get(name, []) for name in _REQUIRED_COLUMNS}
    lengths = {len(values) for values in arrays.values()}
    if len(lengths) > 1:
        raise SECResponseError(
            "SEC submissions columns have mismatched lengths: "
            f"{ {name: len(values) for name, values in arrays.items()} }"
        )

    best: dict[str, str] | None = None
    for index, form in enumerate(arrays["form"]):
        if form != "10-K":
            continue
        row = {name: arrays[name][index] for name in _REQUIRED_COLUMNS}
        if best is None or row["filingDate"] > best["filingDate"]:
            best = row
    return best


def _search_overflow_pages(
    files: list[dict[str, Any]], fetch_page: Callable[[str], dict[str, Any]]
) -> dict[str, str] | None:
    """Fall back to filings.files[] when `recent` (the newest window) has no 10-K.

    Pages are checked newest-first (by filingTo) since a 10-K in `recent` is always
    newer than anything in the overflow pages - this path only runs when `recent`
    came up empty, so the first page with a match is the one we want.
    """
    pages = sorted(files, key=lambda entry: entry.get("filingTo", ""), reverse=True)
    for page_info in pages:
        name = page_info.get("name")
        if not name:
            continue
        page = fetch_page(name)
        row = _latest_10k_row(page)
        if row is not None:
            return row
    return None


def resolve_latest_10k(
    submissions: dict[str, Any],
    *,
    ticker: str,
    company_name: str,
    cik: str,
    settings: Settings,
    fetch_page: Callable[[str], dict[str, Any]] | None = None,
    today: date | None = None,
) -> FilingMetadata:
    """Return the most recent standard 10-K filing's metadata.

    Amendments (10-K/A) and other variants are skipped; only an exact "10-K" form
    match is returned. Falls back to `filings.files[]` overflow pages (fetched via
    `fetch_page`) when `recent` has no 10-K - `recent` only covers roughly the most
    recent year, so a company with a longer filing gap needs the fallback.

    Raises:
        FilingNotFoundError: no 10-K found anywhere searched.
        StaleFilingError: the latest resolvable 10-K is older than
            `settings.max_filing_age_days`.
        SECResponseError: the submissions JSON has an unusable shape.
    """
    filings = submissions.get("filings", {})
    row = _latest_10k_row(filings.get("recent", {}))

    if row is None and fetch_page is not None:
        row = _search_overflow_pages(filings.get("files", []), fetch_page)

    if row is None:
        raise FilingNotFoundError(f"No 10-K found for {ticker} (CIK {cik})")

    effective_today = today or date.today()
    filing_date = date.fromisoformat(row["filingDate"])
    age_days = (effective_today - filing_date).days
    if age_days > settings.max_filing_age_days:
        raise StaleFilingError(
            f"Latest resolvable 10-K for {ticker} is {age_days} days old "
            f"(filed {row['filingDate']}), exceeding the {settings.max_filing_age_days}-day "
            "staleness window"
        )

    cik_unpadded = str(int(cik))
    accession_compact = row["accessionNumber"].replace("-", "")
    document_url = HttpUrl(
        f"{settings.archive_base_url}/{cik_unpadded}/"
        f"{accession_compact}/{row['primaryDocument']}"
    )

    try:
        return FilingMetadata(
            ticker=ticker,
            company_name=company_name,
            cik=cik,
            form_type=row["form"],
            filing_date=filing_date,
            report_date=date.fromisoformat(row["reportDate"]),
            accession_number=row["accessionNumber"],
            primary_document=row["primaryDocument"],
            document_url=document_url,
        )
    except ValidationError as exc:
        raise SECResponseError(
            f"SEC returned malformed filing metadata for {ticker}: {exc}"
        ) from exc
