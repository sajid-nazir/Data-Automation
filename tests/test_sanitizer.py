from unittest.mock import MagicMock

from sec_filings.sanitizer import inline_images, strip_scripts


def test_strip_scripts_removes_normal_script_tag():
    html = '<html><head><script src="/bot-sensor.js"></script></head><body>ok</body></html>'
    result = strip_scripts(html)
    assert "<script" not in result
    assert "<body>ok</body>" in result


def test_strip_scripts_removes_self_closing_script_tag():
    html = '<html><head><script src="/bot-sensor.js"/></head><body>ok</body></html>'
    result = strip_scripts(html)
    assert "<script" not in result


def test_strip_scripts_removes_inline_script_content():
    html = "<html><body>before<script>alert(1)</script>after</body></html>"
    result = strip_scripts(html)
    assert "alert(1)" not in result
    assert "before" in result and "after" in result


def test_strip_scripts_removes_multiple_scripts():
    html = '<script src="a.js"></script>text<script src="b.js"></script>'
    result = strip_scripts(html)
    assert result.count("<script") == 0
    assert "text" in result


def test_inline_images_replaces_relative_src_with_data_uri():
    client = MagicMock()
    client.get_binary.return_value = b"\xff\xd8\xff\xe0fakejpegbytes"

    html = '<img src="aapl-20250927_g1.jpg" alt="logo"/>'
    result = inline_images(html, "https://www.sec.gov/Archives/edgar/data/320193/x/doc.htm", client)

    assert "data:image/jpeg;base64," in result
    assert "aapl-20250927_g1.jpg" not in result
    client.get_binary.assert_called_once_with(
        "https://www.sec.gov/Archives/edgar/data/320193/x/aapl-20250927_g1.jpg"
    )


def test_inline_images_leaves_absolute_and_data_uri_src_untouched():
    client = MagicMock()
    html = (
        '<img src="https://example.com/a.png"/>'
        '<img src="data:image/png;base64,AAAA"/>'
    )
    result = inline_images(html, "https://www.sec.gov/Archives/edgar/data/320193/x/doc.htm", client)

    assert result == html
    client.get_binary.assert_not_called()


def test_inline_images_handles_multiple_relative_images():
    client = MagicMock()
    client.get_binary.side_effect = [b"img1-bytes", b"img2-bytes"]

    html = '<img src="g1.jpg"/><p>text</p><img src="g2.jpg"/>'
    result = inline_images(html, "https://www.sec.gov/Archives/edgar/data/320193/x/doc.htm", client)

    assert result.count("data:image/jpeg;base64,") == 2
    assert client.get_binary.call_count == 2
