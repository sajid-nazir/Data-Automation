import time

import httpx
import pytest

from sec_filings.client import SECClient
from sec_filings.config import Settings
from sec_filings.errors import (
    DisallowedHostError,
    SECBlockedError,
    SECRequestError,
    SECResponseError,
)


def _client_with_transport(settings: Settings, handler) -> SECClient:
    client = SECClient(settings)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
        event_hooks={"request": [client._check_host]},
    )
    return client


# --- basic requests -----------------------------------------------------------


def test_get_submissions_returns_parsed_json(test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == test_settings.user_agent
        return httpx.Response(200, json={"cik": "320193"})

    client = _client_with_transport(test_settings, handler)
    assert client.get_submissions("0000320193") == {"cik": "320193"}


def test_get_submissions_builds_zero_padded_url(test_settings: Settings):
    seen_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={})

    client = _client_with_transport(test_settings, handler)
    client.get_submissions("320193")
    assert seen_urls == [f"{test_settings.submissions_base_url}/CIK0000320193.json"]


def test_get_binary_returns_content_and_content_type(test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"\xff\xd8\xff\xe0binary", headers={"content-type": "image/jpeg"}
        )

    client = _client_with_transport(test_settings, handler)
    content, content_type = client.get_binary("https://www.sec.gov/x/image.jpg")
    assert content == b"\xff\xd8\xff\xe0binary"
    assert content_type == "image/jpeg"


def test_get_submissions_page_validates_name_before_requesting(test_settings: Settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={})

    client = _client_with_transport(test_settings, handler)
    with pytest.raises(SECResponseError):
        client.get_submissions_page("../../etc/passwd")
    assert calls["n"] == 0


def test_get_submissions_page_fetches_valid_name(test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"form": []})

    client = _client_with_transport(test_settings, handler)
    assert client.get_submissions_page("CIK0000886982-submissions-001.json") == {"form": []}


# --- host allowlist -------------------------------------------------------------


def test_refuses_disallowed_host_with_zero_requests(test_settings: Settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="ok")

    client = _client_with_transport(test_settings, handler)
    with pytest.raises(DisallowedHostError):
        client.get_document("https://evil.example/x.htm")
    assert calls["n"] == 0


def test_allows_configured_hosts(test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    client = _client_with_transport(test_settings, handler)
    assert client.get_document("https://www.sec.gov/x.htm") == "ok"
    assert client.get_document("https://data.sec.gov/x.htm") == "ok"


# --- retries ---------------------------------------------------------------------


def test_retries_on_429_then_succeeds(test_settings: Settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, text="ok")

    client = _client_with_transport(test_settings, handler)
    assert client.get_document("https://www.sec.gov/doc.htm") == "ok"
    assert calls["n"] == 3


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_retries_on_server_error_statuses(test_settings: Settings, status: int):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(status)
        return httpx.Response(200, text="ok")

    client = _client_with_transport(test_settings, handler)
    assert client.get_document("https://www.sec.gov/doc.htm") == "ok"
    assert calls["n"] == 2


def test_does_not_retry_on_404(test_settings: Settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    client = _client_with_transport(test_settings, handler)
    with pytest.raises(SECRequestError) as exc_info:
        client.get_document("https://www.sec.gov/doc.htm")
    assert calls["n"] == 1
    assert exc_info.value.status_code == 404


def test_raises_sec_request_error_after_exhausting_retries(test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = _client_with_transport(test_settings, handler)
    with pytest.raises(SECRequestError):
        client.get_document("https://www.sec.gov/doc.htm")


def test_retries_on_timeout_then_succeeds(test_settings: Settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, text="ok")

    client = _client_with_transport(test_settings, handler)
    assert client.get_document("https://www.sec.gov/doc.htm") == "ok"
    assert calls["n"] == 2


def test_does_not_retry_unsupported_protocol(test_settings: Settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.UnsupportedProtocol("bad scheme", request=request)

    client = _client_with_transport(test_settings, handler)
    with pytest.raises(SECRequestError):
        client.get_document("https://www.sec.gov/doc.htm")
    assert calls["n"] == 1


def test_respects_request_deadline(test_settings: Settings):
    settings = test_settings.model_copy(
        update={"request_deadline_seconds": 0.01, "backoff_base_seconds": 10.0, "max_attempts": 10}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = _client_with_transport(settings, handler)
    start = time.monotonic()
    with pytest.raises(SECRequestError):
        client.get_document("https://www.sec.gov/doc.htm")
    assert time.monotonic() - start < 2.0


# --- Retry-After -------------------------------------------------------------------


def test_honors_numeric_retry_after_header(test_settings: Settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "0.02"})
        return httpx.Response(200, text="ok")

    client = _client_with_transport(test_settings, handler)
    start = time.monotonic()
    client.get_document("https://www.sec.gov/doc.htm")
    assert time.monotonic() - start >= 0.02


def test_retry_after_infinite_does_not_crash(test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "inf"})

    client = _client_with_transport(test_settings, handler)
    with pytest.raises(SECRequestError):
        client.get_document("https://www.sec.gov/doc.htm")


def test_retry_after_is_capped_at_max_backoff(test_settings: Settings):
    settings = test_settings.model_copy(
        update={"max_backoff_seconds": 0.02, "max_attempts": 2}
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "3600"})
        return httpx.Response(200, text="ok")

    client = _client_with_transport(settings, handler)
    start = time.monotonic()
    client.get_document("https://www.sec.gov/doc.htm")
    assert time.monotonic() - start < 1.0


# --- block-page detection ------------------------------------------------------


@pytest.mark.parametrize("status", [200, 403])
def test_raises_blocked_error_on_block_page_regardless_of_status(
    test_settings: Settings, status: int
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            text="Your Request Originates from an Undeclared Automated Tool",
            headers={"content-type": "text/html"},
        )

    client = _client_with_transport(test_settings, handler)
    with pytest.raises(SECBlockedError):
        client.get_document("https://www.sec.gov/doc.htm")


def test_403_without_block_marker_raises_plain_request_error(test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden", headers={"content-type": "text/html"})

    client = _client_with_transport(test_settings, handler)
    with pytest.raises(SECRequestError) as exc_info:
        client.get_document("https://www.sec.gov/doc.htm")
    assert not isinstance(exc_info.value, SECBlockedError)
    assert exc_info.value.status_code == 403


def test_block_marker_in_json_response_is_not_flagged(test_settings: Settings):
    # The marker check is scoped to HTML responses; a JSON body containing the
    # same text (extremely unlikely, but worth pinning) must not be misdetected.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"note": "Your Request Originates from an Undeclared Automated Tool"},
        )

    client = _client_with_transport(test_settings, handler)
    assert client.get_submissions("0000320193") is not None


# --- malformed JSON -----------------------------------------------------------


def test_non_json_body_raises_response_error(test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client = _client_with_transport(test_settings, handler)
    with pytest.raises(SECResponseError):
        client.get_submissions("0000320193")


def test_non_dict_json_body_raises_response_error(test_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"])

    client = _client_with_transport(test_settings, handler)
    with pytest.raises(SECResponseError):
        client.get_submissions("0000320193")


# --- throttle ---------------------------------------------------------------------


def test_throttle_enforces_minimum_interval():
    settings = Settings(
        user_agent="Test test@example.com", rate_limit_per_second=10.0, max_attempts=1
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    client = _client_with_transport(settings, handler)
    start = time.monotonic()
    client.get_document("https://www.sec.gov/a.htm")
    client.get_document("https://www.sec.gov/b.htm")
    assert time.monotonic() - start >= 1.0 / settings.rate_limit_per_second
