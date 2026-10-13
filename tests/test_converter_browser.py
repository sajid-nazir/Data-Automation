"""Integration tests against real Chromium (no network) - everything test_converter.py
mocks away is exactly what's exercised here: actual pagination, actual request
routing, actual JS-disabled behavior. Opt-in: `pytest -m browser`."""

from pathlib import Path

import pytest

from sec_filings.client import SECClient
from sec_filings.config import Settings
from sec_filings.converter import HTMLToPDFConverter

pytestmark = pytest.mark.browser


def _page_count(pdf_bytes: bytes) -> int:
    import re

    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes))


def test_page_break_markers_produce_matching_page_count(test_settings: Settings, tmp_path: Path):
    html = """
    <html><body>
    <div>Page one</div><div style="break-after: page"></div>
    <div>Page two</div><div style="break-after: page"></div>
    <div>Page three</div><div style="break-after: page"></div>
    <div>Page four</div>
    </body></html>
    """

    def fetch(url: str) -> tuple[bytes, str]:
        raise AssertionError(f"no resource should be fetched for this document: {url}")

    output_path = tmp_path / "pages.pdf"
    with HTMLToPDFConverter(settings=test_settings) as converter:
        converter.convert("https://www.sec.gov/test.htm", html, output_path, fetch=fetch)

    assert _page_count(output_path.read_bytes()) == 4


def test_foreign_host_image_is_blocked_not_fetched(test_settings: Settings, tmp_path: Path):
    """The converter delegates host enforcement to `fetch` - it has no host
    knowledge of its own. This exercises the real integration: SECClient's host
    allowlist (checked before any network connection) rejects the foreign host,
    and the converter treats that failure as "missing, continue" rather than
    aborting the whole render.
    """
    html = '<html><body><img src="https://evil.example/tracker.png"></body></html>'

    output_path = tmp_path / "foreign-image.pdf"
    with (
        SECClient(test_settings) as client,
        HTMLToPDFConverter(settings=test_settings) as converter,
    ):
        # Must not raise despite the foreign image being refused by the client.
        converter.convert(
            "https://www.sec.gov/test.htm", html, output_path, fetch=client.get_binary
        )

    assert output_path.exists()


def test_inline_script_does_not_execute(test_settings: Settings, tmp_path: Path):
    """A script that would inject a second page-break if it ran must not run.

    This is checked through the real converter's own output (page count), not a
    side channel - if java_script_enabled were accidentally True, this script
    would add a break marker and the PDF would come out as 2 pages instead of 1.
    """
    html = """
    <html><body>
    <div>first page</div>
    <script>
        var hr = document.createElement('div');
        hr.style.breakAfter = 'page';
        document.body.appendChild(hr);
        document.body.appendChild(document.createElement('div')).textContent = 'injected';
    </script>
    </body></html>
    """

    def fetch(url: str) -> tuple[bytes, str]:
        raise AssertionError(f"no resource should be fetched for this document: {url}")

    output_path = tmp_path / "script.pdf"
    with HTMLToPDFConverter(settings=test_settings) as converter:
        converter.convert("https://www.sec.gov/test.htm", html, output_path, fetch=fetch)

    assert _page_count(output_path.read_bytes()) == 1
