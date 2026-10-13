import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sec_filings.config import TARGET_COMPANIES, Settings
from sec_filings.pipeline import main, run


def _submissions_with_10k(
    accession: str, filing_date: str, report_date: str, doc: str
) -> dict[str, Any]:
    return {
        "filings": {
            "recent": {
                "form": ["10-Q", "10-K"],
                "accessionNumber": ["0000320193-24-000150", accession],
                "filingDate": ["2024-11-01", filing_date],
                "reportDate": ["2024-09-28", report_date],
                "primaryDocument": ["aapl-10q.htm", doc],
            },
            "files": [],
        }
    }


def _submissions_with_no_10k() -> dict[str, Any]:
    return {
        "filings": {
            "recent": {
                "form": ["10-Q", "8-K"],
                "accessionNumber": ["0000320193-24-000150", "0000320193-24-000100"],
                "filingDate": ["2024-11-01", "2024-09-01"],
                "reportDate": ["2024-09-28", "2024-08-30"],
                "primaryDocument": ["aapl-10q.htm", "aapl-8k.htm"],
            },
            "files": [],
        }
    }


def _as_context_manager(instance: MagicMock) -> MagicMock:
    """Wrap `instance` so `SomeClass(...)` returns a mock usable as `with SomeClass(...) as x`."""
    cls = MagicMock()
    cls.return_value.__enter__.return_value = instance
    cls.return_value.__exit__.return_value = False
    return cls


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.get_submissions_page.side_effect = AssertionError(
        "get_submissions_page should not be called when `recent` has a 10-K"
    )
    return client


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
    mock_client: MagicMock, mock_converter: MagicMock, test_settings: Settings
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"

    result = run(settings=test_settings)

    assert mock_client.get_submissions.call_count == len(TARGET_COMPANIES)
    called_ciks = {c.args[0] for c in mock_client.get_submissions.call_args_list}
    assert called_ciks == {company["cik"] for company in TARGET_COMPANIES.values()}
    assert mock_converter.convert.call_count == len(TARGET_COMPANIES)
    assert result.succeeded == list(TARGET_COMPANIES)
    assert result.failed == []
    assert result.crashed == []


def test_run_with_explicit_tickers_only_processes_those(
    mock_client: MagicMock, mock_converter: MagicMock, test_settings: Settings
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"

    result = run(["aapl"], settings=test_settings)

    mock_client.get_submissions.assert_called_once_with(TARGET_COMPANIES["AAPL"]["cik"])
    assert mock_converter.convert.call_count == 1
    assert result.succeeded == ["AAPL"]


def test_run_deduplicates_repeated_tickers(
    mock_client: MagicMock, mock_converter: MagicMock, test_settings: Settings
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"

    result = run(["AAPL", "aapl", "AAPL"], settings=test_settings)

    assert mock_client.get_submissions.call_count == 1
    assert result.succeeded == ["AAPL"]


def test_run_fetches_document_and_names_output_file_by_ticker_and_filing_date(
    mock_client: MagicMock, mock_converter: MagicMock, test_settings: Settings, tmp_path: Path
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"

    run(["AAPL"], settings=test_settings)

    expected_url = (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019324000123/aapl-20240928.htm"
    )
    mock_client.get_document.assert_called_once_with(expected_url)
    (doc_url_arg, html_arg, output_path_arg), kwargs = mock_converter.convert.call_args
    assert doc_url_arg == expected_url
    assert html_arg == "<html>filing</html>"
    assert output_path_arg == test_settings.output_dir / "AAPL" / "AAPL_10K_2024-10-01.pdf"
    assert kwargs["fetch"] == mock_client.get_binary


def test_run_continues_after_client_error(
    mock_client: MagicMock, mock_converter: MagicMock, test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
):
    mock_client.get_submissions.side_effect = [
        RuntimeError("SEC unreachable"),
        _submissions_with_10k("0001326801-24-000123", "2024-10-01", "2024-09-28", "meta-10k.htm"),
    ]
    mock_client.get_document.return_value = "<html>filing</html>"

    with caplog.at_level(logging.ERROR):
        result = run(["AAPL", "META"], settings=test_settings)

    assert mock_converter.convert.call_count == 1
    assert result.crashed == ["AAPL"]  # RuntimeError is not a PipelineError -> treated as a bug
    assert result.succeeded == ["META"]


def test_run_continues_after_expected_pipeline_error(
    mock_client: MagicMock, mock_converter: MagicMock, test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
):
    from sec_filings.errors import SECRequestError

    mock_client.get_submissions.side_effect = [
        SECRequestError("SEC unreachable", url="https://www.sec.gov/x"),
        _submissions_with_10k("0001326801-24-000123", "2024-10-01", "2024-09-28", "meta-10k.htm"),
    ]
    mock_client.get_document.return_value = "<html>filing</html>"

    with caplog.at_level(logging.ERROR):
        result = run(["AAPL", "META"], settings=test_settings)

    assert result.failed == ["AAPL"]
    assert result.succeeded == ["META"]
    assert any("AAPL failed" in record.message for record in caplog.records)


def test_run_continues_after_converter_error(
    mock_client: MagicMock, mock_converter: MagicMock, test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
):
    from sec_filings.errors import ConversionError

    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"
    mock_converter.convert.side_effect = [ConversionError("timed out"), None]

    with caplog.at_level(logging.ERROR):
        result = run(["AAPL", "META"], settings=test_settings)

    assert mock_converter.convert.call_count == 2
    assert result.failed == ["AAPL"]
    assert result.succeeded == ["META"]


def test_run_skips_unknown_ticker_without_crashing(
    mock_client: MagicMock, mock_converter: MagicMock, test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.ERROR):
        result = run(["ZZZZ"], settings=test_settings)

    mock_client.get_submissions.assert_not_called()
    mock_converter.convert.assert_not_called()
    assert result.failed == ["ZZZZ"]
    assert any("Unknown ticker 'ZZZZ'" in record.message for record in caplog.records)


def test_run_never_launches_client_or_browser_when_all_tickers_unknown(
    _patch_client_and_converter: tuple[MagicMock, MagicMock], test_settings: Settings
):
    client_cls, converter_cls = _patch_client_and_converter

    run(["ZZZZ", "NOTREAL"], settings=test_settings)

    client_cls.return_value.__enter__.assert_not_called()
    converter_cls.return_value.__enter__.assert_not_called()


def test_run_logs_error_and_fails_when_no_10k_found(
    mock_client: MagicMock, mock_converter: MagicMock, test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
):
    mock_client.get_submissions.return_value = _submissions_with_no_10k()

    with caplog.at_level(logging.ERROR):
        result = run(["AAPL"], settings=test_settings)

    mock_client.get_document.assert_not_called()
    mock_converter.convert.assert_not_called()
    assert result.failed == ["AAPL"]  # regression test: exit-code-0-on-silent-failure


def test_run_counts_every_known_ticker_as_failed_when_browser_fails_to_start(
    mock_client: MagicMock, test_settings: Settings,
    _patch_client_and_converter: tuple[MagicMock, MagicMock],
):
    _client_cls, converter_cls = _patch_client_and_converter
    converter_cls.return_value.__enter__.side_effect = RuntimeError("no browser binary")

    result = run(["AAPL", "META"], settings=test_settings)

    assert sorted(result.failed) == ["AAPL", "META"]
    assert result.succeeded == []


# --- exit codes ---------------------------------------------------------------


def test_main_returns_zero_on_full_success(
    mock_client: MagicMock, mock_converter: MagicMock, monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
):
    mock_client.get_submissions.return_value = _submissions_with_10k(
        "0000320193-24-000123", "2024-10-01", "2024-09-28", "aapl-20240928.htm"
    )
    mock_client.get_document.return_value = "<html>filing</html>"
    monkeypatch.setattr("sec_filings.pipeline.get_settings", lambda: test_settings)

    assert main(["AAPL"]) == 0


def test_main_returns_one_on_expected_failure(
    mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch, test_settings: Settings
):
    monkeypatch.setattr("sec_filings.pipeline.get_settings", lambda: test_settings)
    assert main(["ZZZZ"]) == 1


def test_main_returns_three_on_unexpected_crash(
    mock_client: MagicMock, monkeypatch: pytest.MonkeyPatch, test_settings: Settings
):
    mock_client.get_submissions.side_effect = RuntimeError("bug")
    monkeypatch.setattr("sec_filings.pipeline.get_settings", lambda: test_settings)
    assert main(["AAPL"]) == 3
