import requests

from app.ingestion.source_response_policy import ACTION_CANONICAL_REDIRECT, ACTION_RATE_LIMITED
from app.integrations.rss_client import RSSClient


def _response(
    status_code: int,
    *,
    url: str = "https://example.com/feed",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    history: list[requests.Response] | None = None,
) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.url = url
    response.headers.update(headers or {})
    response._content = body
    response.history = history or []
    return response


def test_rss_fetch_records_429_without_burning_retries(monkeypatch):
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _response(429, headers={"Retry-After": "90"})

    monkeypatch.setenv("CONNECTOR_MAX_RETRIES", "2")
    monkeypatch.setattr(requests, "get", fake_get)

    client = RSSClient(feed_configs=[])
    assert client.fetch_feed("https://example.com/feed") == []

    outcome = client.get_last_fetch_outcome("https://example.com/feed")
    assert calls == 1
    assert outcome is not None
    assert outcome.action == ACTION_RATE_LIMITED
    assert outcome.retry_after_seconds == 90


def test_rss_fetch_records_permanent_redirect(monkeypatch):
    redirect = _response(301, url="http://example.com/feed")
    final = _response(
        200,
        url="https://example.com/feed",
        body=b"""<?xml version="1.0"?>
        <rss><channel><item>
        <title>Example</title><link>https://example.com/post</link>
        <description>Useful tech article summary.</description>
        </item></channel></rss>""",
        history=[redirect],
    )

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: final)

    client = RSSClient(feed_configs=[])
    entries = client.fetch_feed("http://example.com/feed")
    outcome = client.get_last_fetch_outcome("http://example.com/feed")

    assert len(entries) == 1
    assert outcome is not None
    assert outcome.action == ACTION_CANONICAL_REDIRECT
    assert outcome.canonical_url == "https://example.com/feed"
