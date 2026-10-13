import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sec_filings.config import TARGET_COMPANIES, settings
from sec_filings.pipeline import main, run


def _submissions_with_10k(accession: str, filing_date: str, report_date: str, doc: str) -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["10-Q", "10-K"],
                "accessionNumber": ["0000320193-24-000150", accession],
                "filingDate": ["2024-11-01", filing_date],
                "reportDate": ["2024-09-28", report_date],
                "primaryDocument": ["aapl-10q.htm", doc],
            }
        }
    }


def _submissions_with_no_10k() -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["10-Q", "8-K"],
                "accessionNumber": ["0000320193-24-000150", "0000320193-24-000100"],
                "filingDate": ["2024-11-01", "2024-09-01"],
                "reportDate": ["2024-09-28", "2024-08-30"],
                "primaryDocument": ["aapl-10q.htm", "aapl-8k.htm"],
            }
        }
    }


def _as_context_manager(instance: MagicMock) -> MagicMock:
    """Wrap `instance` so `SomeClass(...)` returns a mock usable as `with SomeClass(...) as x`."""
    cls = MagicMock()
    cls.return_value.__enter__.return_value = instance
    cls.return_value.__exit__.return_value = False
    return cls


@pytest.fixture(autouse=True)
def _redirect_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "output_dir", tmp_path)


@pytest.fixture
def mock_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_converter() -> MagicMock:
    return MagicMock()


@pytest.fixture(autouse=True)
def _patch_client_and_converter(mock_client: MagicMock, mock_converter: MagicMock):
    client_cls = _as_context_manager(mock_client)
    converter_cls = _as_context_manager(mock_converter)
    with patch("sec_filings.pipeline.SECClient", client_cls), patch(
        "sec_filings.pipeline.HTMLToPDFConverter", converter_cls
    ):
        yield client_cls, converter_cls


def test_run_defaults_to_all_target_companies_when_no_tickers_given(
    mock_client: MagicMock, mock_converter: MagicMock
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"

    run()

    assert mock_client.get_submissions.call_count == len(TARGET_COMPANIES)
    called_ciks = {c.args[0] for c in mock_client.get_submissions.call_args_list}
    assert called_ciks == {company["cik"] for company in TARGET_COMPANIES.values()}
    assert mock_converter.convert_html_to_pdf.call_count == len(TARGET_COMPANIES)


def test_run_with_explicit_tickers_only_processes_those(
    mock_client: MagicMock, mock_converter: MagicMock
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"

    run(["aapl"])

    mock_client.get_submissions.assert_called_once_with(TARGET_COMPANIES["AAPL"]["cik"])
    assert mock_converter.convert_html_to_pdf.call_count == 1


def test_run_fetches_document_and_names_output_file_by_ticker_and_filing_date(
    mock_client: MagicMock, mock_converter: MagicMock, tmp_path: Path
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"

    run(["AAPL"])

    mock_client.get_document.assert_called_once_with(
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019324000123/aapl-20240928.htm"
    )
    (html_arg, output_path_arg), _ = mock_converter.convert_html_to_pdf.call_args
    assert html_arg == "<html>filing</html>"
    assert output_path_arg == tmp_path / "AAPL" / "AAPL_10K_2024-10-01.pdf"


def test_run_continues_after_client_error(
    mock_client: MagicMock, mock_converter: MagicMock, caplog: pytest.LogCaptureFixture
):
    mock_client.get_submissions.side_effect = [
        RuntimeError("SEC unreachable"),
        _submissions_with_10k("0001326801-24-000123", "2024-10-01", "2024-09-28", "meta-10k.htm"),
    ]
    mock_client.get_document.return_value = "<html>filing</html>"

    with caplog.at_level(logging.ERROR):
        run(["AAPL", "META"])

    assert mock_converter.convert_html_to_pdf.call_count == 1
    assert any("Failed to process AAPL" in record.message for record in caplog.records)


def test_run_continues_after_converter_error(
    mock_client: MagicMock, mock_converter: MagicMock, caplog: pytest.LogCaptureFixture
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"
    mock_converter.convert_html_to_pdf.side_effect = [RuntimeError("timed out"), None]

    with caplog.at_level(logging.ERROR):
        run(["AAPL", "META"])

    assert mock_converter.convert_html_to_pdf.call_count == 2
    assert any("Failed to process AAPL" in record.message for record in caplog.records)


def test_run_skips_unknown_ticker_without_crashing(
    mock_client: MagicMock, mock_converter: MagicMock, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.ERROR):
        run(["ZZZZ"])

    mock_client.get_submissions.assert_not_called()
    mock_converter.convert_html_to_pdf.assert_not_called()
    assert any("Unknown ticker 'ZZZZ'" in record.message for record in caplog.records)


def test_run_never_launches_client_or_browser_when_all_tickers_unknown(
    _patch_client_and_converter: tuple[MagicMock, MagicMock],
):
    client_cls, converter_cls = _patch_client_and_converter

    run(["ZZZZ", "NOTREAL"])

    client_cls.return_value.__enter__.assert_not_called()
    converter_cls.return_value.__enter__.assert_not_called()


def test_run_returns_true_when_every_ticker_succeeds(
    mock_client: MagicMock, mock_converter: MagicMock
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"

    assert run(["AAPL"]) is True


def test_run_returns_false_when_a_ticker_fails(
    mock_client: MagicMock, mock_converter: MagicMock
):
    mock_client.get_submissions.side_effect = RuntimeError("SEC unreachable")

    assert run(["AAPL"]) is False


def test_run_returns_false_when_a_ticker_is_unknown(
    mock_client: MagicMock, mock_converter: MagicMock
):
    assert run(["ZZZZ"]) is False


def test_main_returns_nonzero_exit_code_on_failure(mock_client: MagicMock):
    mock_client.get_submissions.side_effect = RuntimeError("SEC unreachable")

    assert main(["AAPL"]) != 0


def test_main_returns_zero_exit_code_on_success(
    mock_client: MagicMock, mock_converter: MagicMock
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"

    assert main(["AAPL"]) == 0


def test_run_logs_warning_and_skips_conversion_when_no_10k_found(
    mock_client: MagicMock, mock_converter: MagicMock, caplog: pytest.LogCaptureFixture
):
    mock_client.get_submissions.return_value = _submissions_with_no_10k()

    with caplog.at_level(logging.WARNING):
        run(["AAPL"])

    mock_client.get_document.assert_not_called()
    mock_converter.convert_html_to_pdf.assert_not_called()
    assert any("No 10-K found for AAPL" in record.message for record in caplog.records)
