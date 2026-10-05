from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
def test_convert_html_to_pdf_renders_landscape_wide_pdf_from_local_file(
    mock_sync_playwright, tmp_path: Path
):
    factory, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory

    output_path = tmp_path / "AAPL" / "10-K.pdf"

    with HTMLToPDFConverter() as converter:
        result = converter.convert_html_to_pdf("<html><body>filing</body></html>", output_path)

    assert result == output_path
    page.goto.assert_called_once()
    (goto_url,), _ = page.goto.call_args
    assert goto_url.startswith("file://")
    page.pdf.assert_called_once()
    _, pdf_kwargs = page.pdf.call_args
    assert pdf_kwargs["landscape"] is True
    assert pdf_kwargs["format"] == "A3"
    browser.close.assert_called_once()


@patch("src.converter.sync_playwright")
def test_convert_html_to_pdf_cleans_up_temp_file(mock_sync_playwright, tmp_path: Path):
    factory, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory

    with HTMLToPDFConverter() as converter:
        converter.convert_html_to_pdf("<html></html>", tmp_path / "filing.pdf")

    (goto_url,), _ = page.goto.call_args
    tmp_html_path = Path(goto_url.removeprefix("file://"))
    assert not tmp_html_path.exists()


@patch("src.converter.sync_playwright")
def test_convert_raises_conversion_error_when_goto_fails(mock_sync_playwright, tmp_path: Path):
    factory, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory
    page.goto.side_effect = RuntimeError("navigation failed")

    with HTMLToPDFConverter() as converter:
        with pytest.raises(ConversionError):
            converter.convert_html_to_pdf("<html></html>", tmp_path / "filing.pdf")


@patch("src.converter.sync_playwright")
def test_convert_raises_conversion_error_when_pdf_render_fails(mock_sync_playwright, tmp_path: Path):
    factory, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory
    page.pdf.side_effect = RuntimeError("render failed")

    with HTMLToPDFConverter() as converter:
        with pytest.raises(ConversionError):
            converter.convert_html_to_pdf("<html></html>", tmp_path / "filing.pdf")


def test_convert_without_context_manager_raises_runtime_error(tmp_path: Path):
    converter = HTMLToPDFConverter()
    with pytest.raises(RuntimeError):
        converter.convert_html_to_pdf("<html></html>", tmp_path / "filing.pdf")
