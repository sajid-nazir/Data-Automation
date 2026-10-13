"""Playwright-based HTML-to-PDF conversion, tuned for wide financial tables in 10-K filings."""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from types import TracebackType

from playwright.sync_api import Browser, Playwright, sync_playwright

logger = logging.getLogger(__name__)

_PAGE_BREAK_MARKER = "page-break-after"
_PDF_PAGE_OBJECT = re.compile(rb"/Type\s*/Page(?!s)")


class ConversionError(RuntimeError):
    """Raised when Playwright fails to render HTML content as a PDF."""


class HTMLToPDFConverter:
    """Renders already-fetched, self-contained HTML content to PDF.

    Playwright never makes its own request to sec.gov: SEC's edge blocks headless
    Chromium (it announces itself as "HeadlessChrome" via the Sec-CH-UA client-hint
    header on every request), so the HTML must be fetched separately - via SECClient,
    which stays subject to the rate limiter - and pre-processed (scripts stripped,
    images inlined as data URIs by `src.sanitizer`) before reaching this class. It's
    written to a local temp file and rendered from there.

    Used as a context manager so a single browser instance is reused across
    multiple conversions instead of paying launch/teardown cost per document.
    """

    def __init__(self, *, timeout_ms: int = 60_000) -> None:
        self._timeout_ms = timeout_ms
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

    def convert_html_to_pdf(self, html: str, output_path: Path) -> Path:
        """Render `html` to a landscape PDF at `output_path`."""
        if self._browser is None:
            raise RuntimeError(
                "HTMLToPDFConverter must be used as a context manager "
                "(e.g. `with HTMLToPDFConverter(...) as converter:`)"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as tmp_file:
            tmp_file.write(html)
            tmp_path = Path(tmp_file.name)

        context = self._browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(self._timeout_ms)

            try:
                page.goto(tmp_path.as_uri(), wait_until="load", timeout=self._timeout_ms)
            except Exception as exc:
                raise ConversionError(f"Failed to load rendered document: {exc}") from exc

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
            except Exception as exc:
                raise ConversionError(f"Failed to render PDF: {exc}") from exc
        finally:
            context.close()
            tmp_path.unlink(missing_ok=True)

        self._warn_if_likely_truncated(html, output_path)
        return output_path

    def _warn_if_likely_truncated(self, html: str, output_path: Path) -> None:
        """Best-effort check that the PDF isn't silently missing most of the document.

        SEC filings generated via Workiva mark each of their own internal page
        boundaries with `page-break-after`. That count is a reasonable floor for
        how many physical PDF pages a correct render should produce. This has no
        confirmed root cause behind it - a truncation was observed once during
        development and never reproduced - so it only logs, it never raises;
        the goal is to make a repeat silent-truncation visible, not to fail the
        batch on an unconfirmed heuristic.
        """
        expected_min_pages = html.lower().count(_PAGE_BREAK_MARKER)
        if expected_min_pages == 0:
            return

        try:
            actual_pages = len(_PDF_PAGE_OBJECT.findall(output_path.read_bytes()))
        except OSError:
            return

        if actual_pages < max(1, expected_min_pages // 2):
            logger.warning(
                "PDF at %s has %d page(s) but the source document had %d page-break "
                "marker(s) - it may be truncated.",
                output_path, actual_pages, expected_min_pages,
            )
