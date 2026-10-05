from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from src.converter import ConversionError, HTMLToPDFConverter


def _make_mock_playwright():
    """Build a mock `sync_playwright()` chain down to a mock `page`."""
    page = MagicMock()
    context = MagicMock()
    context.new_page.return_value = page
    browser = MagicMock()
    browser.new_context.return_value = context
    playwright_instance = MagicMock()
    playwright_instance.chromium.launch.return_value = browser
    factory = MagicMock()
    factory.start.return_value = playwright_instance
    return factory, browser, context, page


@patch("src.converter.sync_playwright")
def test_convert_url_to_pdf_renders_landscape_wide_pdf(mock_sync_playwright, tmp_path: Path):
    factory, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory

    output_path = tmp_path / "AAPL" / "10-K.pdf"

    with HTMLToPDFConverter(user_agent="Test test@example.com") as converter:
        result = converter.convert_url_to_pdf("https://example.com/filing.htm", output_path)

    assert result == output_path
    page.goto.assert_called_once()
    page.pdf.assert_called_once()
    _, pdf_kwargs = page.pdf.call_args
    assert pdf_kwargs["landscape"] is True
    assert pdf_kwargs["format"] == "A3"
    browser.close.assert_called_once()


@patch("src.converter.sync_playwright")
def test_convert_retries_then_succeeds_after_timeout(mock_sync_playwright, tmp_path: Path):
    factory, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory
    page.goto.side_effect = [PlaywrightTimeoutError("timed out"), None]

    output_path = tmp_path / "filing.pdf"
    with HTMLToPDFConverter(user_agent="Test test@example.com", max_retries=2) as converter:
        converter.convert_url_to_pdf("https://example.com/filing.htm", output_path)

    assert page.goto.call_count == 2


@patch("src.converter.sync_playwright")
def test_convert_raises_conversion_error_after_exhausting_retries(
    mock_sync_playwright, tmp_path: Path
):
    factory, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory
    page.goto.side_effect = PlaywrightTimeoutError("timed out")

    output_path = tmp_path / "filing.pdf"
    with HTMLToPDFConverter(user_agent="Test test@example.com", max_retries=2) as converter:
        with pytest.raises(ConversionError):
            converter.convert_url_to_pdf("https://example.com/filing.htm", output_path)

    assert page.goto.call_count == 2


def test_convert_without_context_manager_raises_runtime_error(tmp_path: Path):
    converter = HTMLToPDFConverter(user_agent="Test test@example.com")
    with pytest.raises(RuntimeError):
        converter.convert_url_to_pdf("https://example.com/filing.htm", tmp_path / "filing.pdf")
