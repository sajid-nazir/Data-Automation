"""CLI orchestrator: fetch -> resolve -> convert -> save to ./output/{ticker}/."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from sec_filings.client import SECClient
from sec_filings.config import TARGET_COMPANIES, Settings, get_settings
from sec_filings.converter import HTMLToPDFConverter
from sec_filings.errors import PipelineError
from sec_filings.resolver import resolve_latest_10k

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


def _process_ticker(
    ticker: str, client: SECClient, converter: HTMLToPDFConverter, settings: Settings
) -> Path:
    """Fetch, resolve, and convert one ticker's latest 10-K. Returns the saved PDF path.

    Raises a PipelineError subclass for any expected failure (no 10-K found, SEC
    request failure, conversion/validation failure) - there is no "partial success"
    return value; a ticker either produces a correct PDF or an exception.
    """
    company = TARGET_COMPANIES[ticker]
    cik = company["cik"]
    company_name = company["name"]

    submissions = client.get_submissions(cik)
    filing = resolve_latest_10k(
        submissions,
        ticker=ticker,
        company_name=company_name,
        cik=cik,
        settings=settings,
        fetch_page=client.get_submissions_page,
    )

    source_url = str(filing.document_url)
    html = client.get_document(source_url)

    output_path = (
        settings.output_dir / ticker / f"{ticker}_10K_{filing.filing_date.isoformat()}.pdf"
    )
    converter.convert(source_url, html, output_path, fetch=client.get_binary)
    logger.info("Saved %s 10-K (%s) to %s", ticker, filing.filing_date, output_path)
    return output_path


@dataclasses.dataclass
class RunResult:
    """Outcome of a batch run. `crashed` (a bug) is distinct from `failed` (an
    expected operational failure) so main()'s exit code can tell them apart."""

    succeeded: list[str] = dataclasses.field(default_factory=list)
    failed: list[str] = dataclasses.field(default_factory=list)
    crashed: list[str] = dataclasses.field(default_factory=list)


def run(tickers: Sequence[str] | None = None, settings: Settings | None = None) -> RunResult:
    """Fetch and convert the latest 10-K for each ticker, isolating failures per ticker."""
    settings = settings or get_settings()

    # dict.fromkeys de-duplicates while preserving first-seen order.
    resolved_tickers = (
        list(dict.fromkeys(t.upper() for t in tickers)) if tickers else list(TARGET_COMPANIES)
    )

    # Validate every ticker against the registry up front, before a client or browser
    # is ever started: a typo shouldn't pay for a Chromium launch before failing.
    unknown = [t for t in resolved_tickers if t not in TARGET_COMPANIES]
    for ticker in unknown:
        logger.error("Unknown ticker %r; not in TARGET_COMPANIES registry", ticker)
    known_tickers = [t for t in resolved_tickers if t not in unknown]

    logger.info(
        "Starting 10-K fetch for %d ticker(s): %s",
        len(resolved_tickers), ", ".join(resolved_tickers),
    )

    result = RunResult(failed=list(unknown))

    if known_tickers:
        already_handled: set[str] = set()
        try:
            with SECClient(settings) as client, HTMLToPDFConverter(
                settings=settings
            ) as converter:
                for ticker in known_tickers:
                    try:
                        _process_ticker(ticker, client, converter, settings)
                    except PipelineError as exc:
                        logger.error("%s failed: %s", ticker, exc)
                        result.failed.append(ticker)
                    except Exception:
                        logger.exception(
                            "Unexpected error processing %s (this is a bug, not an "
                            "expected operational failure)", ticker,
                        )
                        result.crashed.append(ticker)
                    else:
                        result.succeeded.append(ticker)
                    already_handled.add(ticker)
        except Exception:
            # The client or browser itself failed to start (or failed to close
            # cleanly) - no per-ticker loop ran at all, or it was interrupted.
            # Every known ticker not already accounted for is a failure, logged
            # once here rather than as N raw tracebacks.
            logger.exception("Failed to start or clean up SECClient/HTMLToPDFConverter")
            result.failed.extend(t for t in known_tickers if t not in already_handled)

    logger.info(
        "Done: %d succeeded, %d failed, %d crashed%s%s",
        len(result.succeeded), len(result.failed), len(result.crashed),
        f" (failed: {', '.join(result.failed)})" if result.failed else "",
        f" (crashed: {', '.join(result.crashed)})" if result.crashed else "",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Exit codes: 0 all succeeded, 1 any expected failure, 3 any unexpected crash."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = _parse_args(argv)
    result = run(args.tickers or None)
    if result.crashed:
        return 3
    if result.failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
