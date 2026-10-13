"""CLI orchestrator: fetch -> resolve -> convert -> save to ./output/{ticker}/."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from src.client import SECClient
from src.config import TARGET_COMPANIES, settings
from src.converter import HTMLToPDFConverter
from src.resolver import resolve_latest_10k
from src.sanitizer import inline_images, strip_scripts

logger = logging.getLogger(__name__)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch the latest 10-K filing for each ticker and convert it to PDF.",
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help="Ticker symbols to fetch (default: all configured target companies).",
    )
    return parser.parse_args(argv)


def _process_ticker(ticker: str, client: SECClient, converter: HTMLToPDFConverter) -> bool:
    """Fetch, resolve, and convert one ticker's latest 10-K. Returns True iff a PDF was saved."""
    company = TARGET_COMPANIES[ticker]
    cik = company["cik"]
    company_name = company["name"]

    submissions = client.get_submissions(cik)
    filing = resolve_latest_10k(submissions, ticker=ticker, company_name=company_name, cik=cik)
    if filing is None:
        logger.warning("No 10-K found for %s", ticker)
        return False

    source_url = str(filing.document_url)
    html = client.get_document(source_url)
    html = strip_scripts(html)
    html = inline_images(html, source_url, client)

    output_path = (
        settings.output_dir / ticker / f"{ticker}_10K_{filing.filing_date.isoformat()}.pdf"
    )
    converter.convert_html_to_pdf(html, output_path)
    logger.info("Saved %s 10-K (%s) to %s", ticker, filing.filing_date, output_path)
    return True


def run(tickers: Sequence[str] | None = None) -> bool:
    """Fetch and convert the latest 10-K for each ticker, skipping failures individually.

    Returns True iff every requested ticker produced a saved PDF.
    """
    resolved_tickers = [t.upper() for t in tickers] if tickers else list(TARGET_COMPANIES)

    # Validate every ticker against the registry up front, before a client or browser
    # is ever started: a typo shouldn't pay for a Chromium launch before failing.
    unknown = [t for t in resolved_tickers if t not in TARGET_COMPANIES]
    for ticker in unknown:
        logger.error("Unknown ticker %r; not in TARGET_COMPANIES registry", ticker)
    known_tickers = [t for t in resolved_tickers if t in TARGET_COMPANIES]

    logger.info(
        "Starting 10-K fetch for %d ticker(s): %s", len(resolved_tickers), ", ".join(resolved_tickers)
    )

    succeeded: list[str] = []
    skipped: list[str] = []
    failed: list[str] = list(unknown)

    if known_tickers:
        with SECClient() as client, HTMLToPDFConverter(
            timeout_ms=settings.pdf_timeout_ms
        ) as converter:
            for ticker in known_tickers:
                try:
                    saved = _process_ticker(ticker, client, converter)
                except Exception:
                    # Broad by design: one company's failure (SEC error, conversion
                    # timeout) must never abort the rest of the batch.
                    logger.error("Failed to process %s", ticker, exc_info=True)
                    failed.append(ticker)
                else:
                    (succeeded if saved else skipped).append(ticker)

    logger.info(
        "Done: %d succeeded, %d skipped, %d failed%s",
        len(succeeded),
        len(skipped),
        len(failed),
        f" ({', '.join(failed)})" if failed else "",
    )

    return not failed


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)
    ok = run(args.tickers or None)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
