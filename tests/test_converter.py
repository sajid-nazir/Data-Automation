from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sec_filings.config import Settings
from sec_filings.converter import ConversionError, HTMLToPDFConverter, PDFValidationError


def _fake_pdf_bytes(n_pages: int) -> bytes:
    return b"\n".join([b"<< /Type /Page >>"] * n_pages)


def _make_mock_playwright():
    page = MagicMock()
    page.evaluate.return_value = 0  # 0 break markers -> expected_pages = 1
    page.pdf.return_value = _fake_pdf_bytes(1)
    context = MagicMock()
    context.new_page.return_value = page
    browser = MagicMock()
    browser.is_connected.return_value = True
    browser.new_context.return_value = context
    playwright_instance = MagicMock()
    playwright_instance.chromium.launch.return_value = browser
    factory = MagicMock()
    factory.start.return_value = playwright_instance
    return factory, playwright_instance, browser, context, page


def _get_route_handler(context: MagicMock):
    (_, handler), _ = context.route.call_args
    return handler


@pytest.fixture
def fetch_stub():
    def fetch(url: str) -> tuple[bytes, str]:
        return b"fake-bytes", "image/jpeg"

    return fetch


# --- basic rendering ---------------------------------------------------------


@patch("sec_filings.converter.sync_playwright")
def test_convert_renders_letter_portrait_pdf(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory

    output_path = tmp_path / "AAPL" / "10-K.pdf"
    with HTMLToPDFConverter(settings=test_settings) as converter:
        result = converter.convert(
            "https://www.sec.gov/doc.htm", "<html></html>", output_path, fetch=fetch_stub
        )

    assert result == output_path
    assert output_path.exists()
    assert not output_path.with_name(output_path.name + ".part").exists()
    page.goto.assert_called_once_with(
        "https://www.sec.gov/doc.htm", wait_until="load", timeout=test_settings.pdf_timeout_ms
    )
    _, pdf_kwargs = page.pdf.call_args
    assert pdf_kwargs["landscape"] is False
    assert pdf_kwargs["format"] == "Letter"
    browser.close.assert_called_once()


@patch("sec_filings.converter.sync_playwright")
def test_context_disables_javascript(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory

    with HTMLToPDFConverter(settings=test_settings) as converter:
        converter.convert("https://www.sec.gov/doc.htm", "<html></html>", tmp_path / "f.pdf", fetch=fetch_stub)

    _, context_kwargs = browser.new_context.call_args
    assert context_kwargs["java_script_enabled"] is False


# --- route handler: this is the network-isolation boundary ---------------------


@patch("sec_filings.converter.sync_playwright")
def test_route_handler_fulfills_document_url_with_given_html(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory

    with HTMLToPDFConverter(settings=test_settings) as converter:
        converter.convert(
            "https://www.sec.gov/doc.htm", "<html>BODY</html>", tmp_path / "f.pdf", fetch=fetch_stub
        )

    handler = _get_route_handler(context)
    route = MagicMock()
    route.request.url = "https://www.sec.gov/doc.htm"
    handler(route)

    route.fulfill.assert_called_once_with(
        body="<html>BODY</html>", content_type="text/html; charset=utf-8"
    )


@patch("sec_filings.converter.sync_playwright")
def test_route_handler_fulfills_image_via_fetch(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory

    with HTMLToPDFConverter(settings=test_settings) as converter:
        converter.convert("https://www.sec.gov/doc.htm", "<html></html>", tmp_path / "f.pdf", fetch=fetch_stub)

    handler = _get_route_handler(context)
    route = MagicMock()
    route.request.url = "https://www.sec.gov/image.jpg"
    route.request.resource_type = "image"
    handler(route)

    route.fulfill.assert_called_once_with(body=b"fake-bytes", content_type="image/jpeg")


@patch("sec_filings.converter.sync_playwright")
def test_route_handler_aborts_disallowed_resource_types(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory

    with HTMLToPDFConverter(settings=test_settings) as converter:
        converter.convert("https://www.sec.gov/doc.htm", "<html></html>", tmp_path / "f.pdf", fetch=fetch_stub)

    handler = _get_route_handler(context)
    route = MagicMock()
    route.request.url = "https://www.sec.gov/script.js"
    route.request.resource_type = "script"
    handler(route)

    route.abort.assert_called_once()
    route.fulfill.assert_not_called()


@patch("sec_filings.converter.sync_playwright")
def test_route_handler_aborts_and_render_continues_when_fetch_fails(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory

    def failing_fetch(url: str) -> tuple[bytes, str]:
        raise RuntimeError("404")

    output_path = tmp_path / "f.pdf"
    with HTMLToPDFConverter(settings=test_settings) as converter:
        result = converter.convert(
            "https://www.sec.gov/doc.htm", "<html></html>", output_path, fetch=failing_fetch
        )

    assert result == output_path
    assert output_path.exists()

    handler = _get_route_handler(context)
    route = MagicMock()
    route.request.url = "https://www.sec.gov/image.jpg"
    route.request.resource_type = "image"
    handler(route)

    route.abort.assert_called_once()
    route.fulfill.assert_not_called()


# --- truncation / scale ladder --------------------------------------------------


@patch("sec_filings.converter.sync_playwright")
def test_raises_pdf_validation_error_when_rendered_pages_fall_short_of_markers(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory
    page.evaluate.return_value = 60  # expected_pages = 61
    page.pdf.return_value = _fake_pdf_bytes(8)  # the historical 8-of-61 truncation

    output_path = tmp_path / "f.pdf"
    with HTMLToPDFConverter(settings=test_settings) as converter:
        with pytest.raises(PDFValidationError):
            converter.convert("https://www.sec.gov/doc.htm", "<html></html>", output_path, fetch=fetch_stub)

    assert not output_path.exists()
    assert not output_path.with_name(output_path.name + ".part").exists()


@patch("sec_filings.converter.sync_playwright")
def test_scale_ladder_retries_on_overflow_then_accepts(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory
    page.evaluate.return_value = 9  # expected_pages = 10
    page.pdf.side_effect = [_fake_pdf_bytes(20), _fake_pdf_bytes(10)]

    output_path = tmp_path / "f.pdf"
    with HTMLToPDFConverter(settings=test_settings) as converter:
        result = converter.convert("https://www.sec.gov/doc.htm", "<html></html>", output_path, fetch=fetch_stub)

    assert page.pdf.call_count == 2
    assert result == output_path
    assert output_path.exists()


@patch("sec_filings.converter.sync_playwright")
def test_scale_ladder_raises_when_every_scale_overflows(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory
    page.evaluate.return_value = 9  # expected_pages = 10
    page.pdf.return_value = _fake_pdf_bytes(50)  # overflows at every scale

    with HTMLToPDFConverter(settings=test_settings) as converter:
        with pytest.raises(PDFValidationError):
            converter.convert("https://www.sec.gov/doc.htm", "<html></html>", tmp_path / "f.pdf", fetch=fetch_stub)

    assert page.pdf.call_count == len(test_settings.pdf_scales)


# --- error propagation -----------------------------------------------------------


@patch("sec_filings.converter.sync_playwright")
def test_convert_raises_conversion_error_when_goto_fails(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory
    page.goto.side_effect = RuntimeError("navigation failed")

    with HTMLToPDFConverter(settings=test_settings) as converter:
        with pytest.raises(ConversionError):
            converter.convert("https://www.sec.gov/doc.htm", "<html></html>", tmp_path / "f.pdf", fetch=fetch_stub)


@patch("sec_filings.converter.sync_playwright")
def test_convert_raises_conversion_error_when_pdf_render_fails(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory
    page.pdf.side_effect = RuntimeError("render failed")

    with HTMLToPDFConverter(settings=test_settings) as converter:
        with pytest.raises(ConversionError):
            converter.convert("https://www.sec.gov/doc.htm", "<html></html>", tmp_path / "f.pdf", fetch=fetch_stub)


def test_convert_without_context_manager_raises_runtime_error(
    test_settings: Settings, tmp_path: Path, fetch_stub
):
    converter = HTMLToPDFConverter(settings=test_settings)
    with pytest.raises(RuntimeError):
        converter.convert("https://www.sec.gov/doc.htm", "<html></html>", tmp_path / "f.pdf", fetch=fetch_stub)


# --- lifecycle / resource cleanup ------------------------------------------------


@patch("sec_filings.converter.sync_playwright")
def test_playwright_is_stopped_if_browser_launch_fails(mock_sync_playwright, test_settings: Settings):
    playwright_instance = MagicMock()
    playwright_instance.chromium.launch.side_effect = RuntimeError("no browser binary")
    factory = MagicMock()
    factory.start.return_value = playwright_instance
    mock_sync_playwright.return_value = factory

    converter = HTMLToPDFConverter(settings=test_settings)
    with pytest.raises(RuntimeError):
        converter.__enter__()

    playwright_instance.stop.assert_called_once()


@patch("sec_filings.converter.sync_playwright")
def test_relaunches_browser_if_disconnected(
    mock_sync_playwright, test_settings: Settings, tmp_path: Path, fetch_stub
):
    factory, pw, browser, context, page = _make_mock_playwright()
    mock_sync_playwright.return_value = factory
    browser.is_connected.return_value = False

    new_browser = MagicMock()
    new_browser.is_connected.return_value = True
    new_context = MagicMock()
    new_context.new_page.return_value = page
    new_browser.new_context.return_value = new_context
    pw.chromium.launch.side_effect = [browser, new_browser]

    with HTMLToPDFConverter(settings=test_settings) as converter:
        converter.convert("https://www.sec.gov/doc.htm", "<html></html>", tmp_path / "f.pdf", fetch=fetch_stub)

    new_browser.new_context.assert_called_once()
