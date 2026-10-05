"""Playwright-based HTML-to-PDF conversion, tuned for wide financial tables in 10-K filings."""

from __future__ import annotations

import logging
from pathlib import Path
from types import TracebackType

from playwright.sync_api import Browser, Playwright, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


class ConversionError(RuntimeError):
    """Raised when Playwright fails to load or render a filing document as a PDF."""


class HTMLToPDFConverter:
    """Renders a remote filing document to PDF.

    Used as a context manager so a single browser instance is reused across
    multiple conversions (one filing per target company) instead of paying
    launch/teardown cost per document.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_ms: int = 60_000,
        max_retries: int = 2,
    ) -> None:
        self._user_agent = user_agent
        self._timeout_ms = timeout_ms
        self._max_retries = max_retries
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> "HTMLToPDFConverter":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def convert_url_to_pdf(self, url: str, output_path: Path) -> Path:
        """Load `url` and render it to a landscape PDF at `output_path`."""
        if self._browser is None:
            raise RuntimeError(
                "HTMLToPDFConverter must be used as a context manager "
                "(e.g. `with HTMLToPDFConverter(...) as converter:`)"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        context = self._browser.new_context(user_agent=self._user_agent)
        try:
            page = context.new_page()
            page.set_default_timeout(self._timeout_ms)

            last_error: Exception | None = None
            for attempt in range(1, self._max_retries + 1):
                try:
                    page.goto(url, wait_until="networkidle", timeout=self._timeout_ms)
                    break
                except PlaywrightTimeoutError as exc:
                    last_error = exc
                    logger.warning(
                        "Timeout loading %s (attempt %d/%d)", url, attempt, self._max_retries
                    )
            else:
                raise ConversionError(
                    f"Failed to load {url} after {self._max_retries} attempt(s)"
                ) from last_error

            try:
                page.emulate_media(media="print")
                page.pdf(
                    path=str(output_path),
                    format="A3",
                    landscape=True,
                    print_background=True,
                    scale=0.8,
                    prefer_css_page_size=False,
                    margin={"top": "10mm", "right": "8mm", "bottom": "10mm", "left": "8mm"},
                )
            except Exception as exc:  # noqa: BLE001 - surfaced as a typed ConversionError
                raise ConversionError(f"Failed to render PDF for {url}: {exc}") from exc
        finally:
            context.close()

        return output_path
