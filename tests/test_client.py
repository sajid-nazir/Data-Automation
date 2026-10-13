import time

import httpx
import pytest

from sec_filings.client import SECBlockedError, SECClient, SECRequestError
from sec_filings.config import Settings


@pytest.fixture
def fast_settings() -> Settings:
    """Settings tuned so retry/backoff tests don't actually sleep for real."""
    return Settings(
        rate_limit_per_second=1000.0,
        max_retries=3,
        backoff_base_seconds=0.001,
        request_timeout_seconds=5.0,
    )


def _client_with_transport(settings: Settings, handler) -> SECClient:
    client = SECClient(settings)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": settings.user_agent},
    )
    return client


def test_get_submissions_returns_parsed_json(fast_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == fast_settings.user_agent
        return httpx.Response(200, json={"cik": "320193"})

    client = _client_with_transport(fast_settings, handler)
    assert client.get_submissions("0000320193") == {"cik": "320193"}


def test_get_submissions_builds_zero_padded_url(fast_settings: Settings):
    seen_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={})

    client = _client_with_transport(fast_settings, handler)
    client.get_submissions("320193")
    assert seen_urls == [f"{fast_settings.submissions_base_url}/CIK0000320193.json"]


def test_retries_on_429_then_succeeds(fast_settings: Settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, text="ok")

    client = _client_with_transport(fast_settings, handler)
    assert client.get_document("https://example.test/doc.htm") == "ok"
    assert calls["n"] == 3


def test_retries_on_503_and_500_and_502_and_504(fast_settings: Settings):
    for status in (500, 502, 503, 504):
        calls = {"n": 0}

        def handler(request: httpx.Request, status=status) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(status)
            return httpx.Response(200, text="ok")

        client = _client_with_transport(fast_settings, handler)
        assert client.get_document("https://example.test/doc.htm") == "ok"


def test_does_not_retry_on_404(fast_settings: Settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    client = _client_with_transport(fast_settings, handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_document("https://example.test/doc.htm")
    assert calls["n"] == 1


def test_raises_sec_request_error_after_exhausting_retries(fast_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = _client_with_transport(fast_settings, handler)
    with pytest.raises(SECRequestError):
        client.get_document("https://example.test/doc.htm")


def test_honors_retry_after_header(fast_settings: Settings):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "0.02"})
        return httpx.Response(200, text="ok")

    client = _client_with_transport(fast_settings, handler)
    start = time.monotonic()
    client.get_document("https://example.test/doc.htm")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.02


def test_get_document_raises_on_block_page(fast_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="Your Request Originates from an Undeclared Automated Tool"
        )

    client = _client_with_transport(fast_settings, handler)
    with pytest.raises(SECBlockedError):
        client.get_document("https://example.test/doc.htm")


def test_get_binary_returns_raw_bytes(fast_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0binary-jpeg-bytes")

    client = _client_with_transport(fast_settings, handler)
    assert client.get_binary("https://example.test/image.jpg") == b"\xff\xd8\xff\xe0binary-jpeg-bytes"


def test_throttle_enforces_minimum_interval():
    settings = Settings(rate_limit_per_second=20.0, max_retries=1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    client = _client_with_transport(settings, handler)
    start = time.monotonic()
    client.get_document("https://example.test/a.htm")
    client.get_document("https://example.test/b.htm")
    elapsed = time.monotonic() - start
    assert elapsed >= 1.0 / settings.rate_limit_per_second
