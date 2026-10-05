"""CLI orchestrator: fetch -> resolve -> convert -> save to ./output/{ticker}/.

Scaffold only — wires together client.SECClient, resolver.resolve_latest_10k, and
converter.HTMLToPDFConverter for each entry in config.TARGET_COMPANIES. Not yet
implemented.
"""

from __future__ import annotations

from src.config import TARGET_COMPANIES, settings


def run() -> None:
    """Fetch the latest 10-K for every company in TARGET_COMPANIES and save it as a PDF."""
    raise NotImplementedError


def main() -> None:
    run()


if __name__ == "__main__":
    main()
