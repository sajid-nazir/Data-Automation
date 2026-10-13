"""Playwright-based HTML-to-PDF conversion, tuned for wide financial tables in 10-K filings."""

from __future__ import annotations

import contextlib
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from playwright.sync_api import Browser, Page, Playwright, Route, sync_playwright

from sec_filings.config import Settings
from sec_filings.errors import ConversionError, PDFValidationError

logger = logging.getLogger(__name__)

_PDF_PAGE_OBJECT = re.compile(rb"/Type\s*/Page(?!s)")

# Resource types the renderer will actually serve. Everything else (scripts, XHR,
# fonts-via-script, favicons, "other") is aborted - there is no code path that
# fetches it, since JS never runs to request it either.
_RENDERABLE_RESOURCE_TYPES = {"document", "image", "stylesheet", "font"}

# Counts elements whose *computed* break-after lands on a page boundary. Works with
# JS disabled: Playwright's page.evaluate runs via its own DevTools Protocol channel,
# independent of the page's javascript_enabled setting.
_COUNT_PAGE_BREAKS_JS = """
() => {
    let count = 0;
    for (const el of document.querySelectorAll('*')) {
        const style = getComputedStyle(el);
        const breakAfter = style.breakAfter || style.pageBreakAfter;
        if (breakAfter === 'page' || breakAfter === 'always') count++;
    }
    return count;
}
"""


class HTMLToPDFConverter:
    """Renders a filing to PDF with no open network access.

    Javascript is disabled, and every request the page makes is routed through an
    explicit `fetch` callback supplied by the caller (backed by SECClient, so it
    stays rate-limited and host-allowlisted) rather than going to the network
    directly - the document itself is served from already-fetched HTML, not a live
    URL. Playwright's own request headers (which previously got the renderer
    blocked by SEC - see README) never reach a real server at all.

    Used as a context manager so a single browser instance is reused across
    multiple conversions instead of paying launch/teardown cost per document.
    """

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._timeout_ms = settings.pdf_timeout_ms
        self._stack: contextlib.ExitStack | None = None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> HTMLToPDFConverter:
        stack = contextlib.ExitStack()
        try:
            self._playwright = sync_playwright().start()
            stack.callback(self._playwright.stop)
            self._browser = self._playwright.chromium.launch()
            stack.callback(self._browser.close)
        except Exception:
            stack.close()
            raise
        self._stack = stack
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stack is not None:
            self._stack.close()

    def _ensure_browser(self) -> Browser:
        if self._browser is None or self._playwright is None:
            raise RuntimeError(
                "HTMLToPDFConverter must be used as a context manager "
                "(e.g. `with HTMLToPDFConverter(...) as converter:`)"
            )
        if not self._browser.is_connected():
            logger.warning("Chromium had disconnected; relaunching for the next conversion")
            self._browser = self._playwright.chromium.launch()
        return self._browser

    def convert(
        self,
        document_url: str,
        html: str,
        output_path: Path,
        *,
        fetch: Callable[[str], tuple[bytes, str]],
    ) -> Path:
        """Render `html` (already fetched from `document_url`) to a PDF at `output_path`.

        `document_url` is used only as a label the route handler recognizes to serve
        `html` - Playwright never fetches it. Every other resource the page requests
        (images, stylesheets, fonts) goes through `fetch`; anything that fails to
        fetch is logged and the render continues without it rather than failing the
        whole filing over one missing image.
        """
        browser = self._ensure_browser()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        missing: list[str] = []

        def handle_route(route: Route) -> None:
            url = route.request.url
            if url == document_url:
                route.fulfill(body=html, content_type="text/html; charset=utf-8")
                return
            if route.request.resource_type not in _RENDERABLE_RESOURCE_TYPES:
                route.abort()
                return
            try:
                content, content_type = fetch(url)
            except Exception as exc:
                logger.warning("Failed to fetch %s while rendering: %s", url, exc)
                missing.append(url)
                route.abort()
                return
            route.fulfill(body=content, content_type=content_type)

        context = browser.new_context(java_script_enabled=False, service_workers="block")
        try:
            context.route("**/*", handle_route)
            page = context.new_page()
            page.set_default_timeout(self._timeout_ms)

            try:
                page.goto(document_url, wait_until="load", timeout=self._timeout_ms)
            except Exception as exc:
                raise ConversionError(f"Failed to load {document_url}: {exc}") from exc

            if missing:
                logger.warning(
                    "%d resource(s) failed to load while rendering %s: %s",
                    len(missing), document_url, ", ".join(missing),
                )

            page.emulate_media(media="print")
            expected_pages = page.evaluate(_COUNT_PAGE_BREAKS_JS) + 1

            pdf_bytes, pages, scale = self._render_with_scale_ladder(page, expected_pages)
        finally:
            context.close()

        self._write_atomically(output_path, pdf_bytes)

        logger.info(
            "Rendered %s: %d page(s) at scale %.1f (source has %d page-break marker(s))",
            output_path, pages, scale, expected_pages - 1,
        )
        return output_path

    def _render_with_scale_ladder(
        self, page: Page, expected_pages: int
    ) -> tuple[bytes, int, float]:
        """Try each configured scale until the page count is within tolerance.

        A page count *below* `expected_pages` fails immediately at any scale - that's
        not an overflow to fix by shrinking further, it's missing content (the
        failure mode observed once during development: 8 rendered pages against 61
        page-break markers, with no error of any kind). A page count *above*
        tolerance is retried at the next smaller scale.
        """
        tolerance = max(expected_pages * 1.03, expected_pages + 1)
        last_pages = 0

        for scale in self._settings.pdf_scales:
            try:
                pdf_bytes = page.pdf(
                    format="Letter",
                    landscape=False,
                    scale=scale,
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "0.4in", "right": "0.4in", "bottom": "0.4in", "left": "0.4in"},
                )
            except Exception as exc:
                raise ConversionError(f"Failed to render PDF: {exc}") from exc

            pages = len(_PDF_PAGE_OBJECT.findall(pdf_bytes))
            last_pages = pages

            if pages < expected_pages:
                raise PDFValidationError(
                    f"Rendered PDF has {pages} page(s) but the source document has "
                    f"{expected_pages} page-break marker(s) - likely truncated"
                )
            if pages <= tolerance:
                return pdf_bytes, pages, scale

        raise PDFValidationError(
            f"Rendered PDF overflowed at every scale in {self._settings.pdf_scales}: "
            f"{last_pages} page(s), expected <= {tolerance:.0f} "
            f"({expected_pages} page-break marker(s) in the source)"
        )

    def _write_atomically(self, output_path: Path, content: bytes) -> None:
        tmp_path = output_path.with_name(output_path.name + ".part")
        try:
            tmp_path.write_bytes(content)
            os.replace(tmp_path, output_path)
        finally:
            tmp_path.unlink(missing_ok=True)
