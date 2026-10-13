"""End-to-end against the real SEC API and real Chromium. Opt-in: `pytest -m live`.

Requires SEC_USER_AGENT to be set (see README) and real internet access. Not run
by default - CI or a developer runs this explicitly before trusting a change to
client.py/resolver.py/converter.py that the mocked unit tests can't fully cover.
"""

import pytest

from sec_filings.config import TARGET_COMPANIES, get_settings
from sec_filings.pipeline import run

pytestmark = pytest.mark.live


def _pdf_page_count(path) -> int:
    import re

    return len(re.findall(rb"/Type\s*/Page(?!s)", path.read_bytes()))


def test_full_pipeline_against_real_sec_for_every_target_company(tmp_path, monkeypatch):
    settings = get_settings().model_copy(update={"output_dir": tmp_path})
    monkeypatch.setattr("sec_filings.pipeline.get_settings", lambda: settings)

    result = run(settings=settings)

    assert result.failed == []
    assert result.crashed == []
    assert sorted(result.succeeded) == sorted(TARGET_COMPANIES)

    for ticker in TARGET_COMPANIES:
        matches = list((tmp_path / ticker).glob(f"{ticker}_10K_*.pdf"))
        assert len(matches) == 1, f"expected exactly one PDF for {ticker}, found {matches}"

        pdf_path = matches[0]
        pages = _pdf_page_count(pdf_path)
        # Loose bound: a real 10-K is never a handful of pages (the historical
        # truncation bug) and never absurdly huge (a render gone wrong). The scale
        # ladder's own tolerance (converter.py) is the precise check; this is a
        # coarse sanity net on top of it.
        assert 20 <= pages <= 400, f"{ticker}: {pages} page(s) looks implausible for a 10-K"
        assert pdf_path.stat().st_size > 50_000, f"{ticker}: PDF suspiciously small"


def test_pdf_page_size_is_letter_portrait(tmp_path, monkeypatch):
    settings = get_settings().model_copy(update={"output_dir": tmp_path})
    monkeypatch.setattr("sec_filings.pipeline.get_settings", lambda: settings)

    run(["AAPL"], settings=settings)

    pdf_path = next((tmp_path / "AAPL").glob("AAPL_10K_*.pdf"))
    content = pdf_path.read_bytes()
    # Letter at 72dpi is 612x792pt; confirm the page box matches, not A3 landscape.
    assert b"/MediaBox[0 0 612 792]" in content or b"612 792" in content
