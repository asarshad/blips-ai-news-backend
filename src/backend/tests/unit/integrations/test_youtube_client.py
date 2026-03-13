import time
from unittest.mock import Mock

import pytest

from app.integrations.youtube_client import YouTubeClient

pytestmark = [pytest.mark.unit]


def test_extract_video_id_from_multiple_url_forms():
    client = YouTubeClient(channel_configs=[])

    assert client._extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert client._extract_video_id("https://youtu.be/dQw4w9WgXcQ?t=43") == "dQw4w9WgXcQ"
    assert client._extract_video_id("https://www.youtube.com/shorts/abc123DEF45") == "abc123DEF45"


def test_categorize_video_prefers_keyword_matches():
    client = YouTubeClient(channel_configs=[])

    category = client._categorize_video(
        title="OpenAI releases new model",
        summary="We talk about LLMs and machine learning advances.",
    )
    assert category in {"Artificial Intelligence", "AI Tools", "Developer & Engineering"}


def test_get_summary_uses_transcript_when_missing_description(monkeypatch):
    client = YouTubeClient(channel_configs=[])

    class Entry(dict):
        pass

    entry = Entry(
        link="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Test video",
        author="Example",
    )

    monkeypatch.setattr(client, "get_transcript", lambda video_id: "transcript text " * 100)
    summary = client._get_summary(entry)

    assert "transcript text" in summary
    assert len(summary) <= 5000


def test_parse_feed_with_retries_succeeds_after_one_retry(monkeypatch, fixture_text):
    xml = fixture_text("youtube/sample_channel.xml").encode("utf-8")
    client = YouTubeClient(channel_configs=[])
    monkeypatch.setattr(client, "_max_retries", 2)
    monkeypatch.setattr(client, "_retry_budget_remaining", 2)
    monkeypatch.setattr(client, "_timeout_seconds", 1.0)
    monkeypatch.setattr(client, "_backoff_base_seconds", 0.0)

    calls = {"n": 0}
    import requests

    def _fake_get(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.RequestException("transient")
        resp = Mock()
        resp.content = xml
        resp.raise_for_status.return_value = None
        return resp

    monkeypatch.setattr(requests, "get", _fake_get)
    feed = client._parse_feed_with_retries("https://www.youtube.com/feeds/videos.xml?channel_id=x")

    assert calls["n"] == 2
    assert len(feed.entries) == 2


def test_parse_feed_with_retries_stops_when_budget_exhausted(monkeypatch):
    client = YouTubeClient(channel_configs=[])
    monkeypatch.setattr(client, "_max_retries", 3)
    monkeypatch.setattr(client, "_retry_budget_remaining", 0)
    monkeypatch.setattr(client, "_timeout_seconds", 1.0)
    monkeypatch.setattr(client, "_backoff_base_seconds", 0.0)

    import requests

    def _always_fail(*_args, **_kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "get", _always_fail)
    feed = client._parse_feed_with_retries("https://www.youtube.com/feeds/videos.xml?channel_id=x")
    assert len(feed.entries) == 0


def test_parse_entry_published_at_prefers_published_then_updated():
    client = YouTubeClient(channel_configs=[])

    published_entry = {"published_parsed": time.gmtime(1716912000)}
    updated_entry = {"updated_parsed": time.gmtime(1716998400)}
    empty_entry = {}

    published_at = client._parse_entry_published_at(published_entry)
    updated_at = client._parse_entry_published_at(updated_entry)

    assert published_at is not None
    assert published_at.year == 2024
    assert updated_at is not None
    assert updated_at.year == 2024
    assert client._parse_entry_published_at(empty_entry) is None


class _FakeQuotaBudget:
    def __init__(self, *, locked: bool = False, begin_window: bool = True):
        self.locked = locked
        self.begin_window = begin_window
        self.reserve_calls = []
        self.lockout_reasons = []

    def try_reserve(self, units, *, bucket="general"):
        self.reserve_calls.append((units, bucket))
        return not self.locked

    def is_locked_out(self):
        return self.locked

    def lock_out_until_reset(self, *, reason="quotaExceeded"):
        self.locked = True
        self.lockout_reasons.append(reason)

    def begin_search_window(self, _surface):
        return self.begin_window


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_search_candidates_uses_header_auth_and_no_query_key(monkeypatch):
    quota = _FakeQuotaBudget()
    client = YouTubeClient(channel_configs=[], quota_budget=quota)
    captured = {}

    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")

    def _fake_get(url, *, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResponse(
            200,
            {
                "items": [
                    {"id": {"videoId": "abc123DEF45"}},
                    {"id": {"videoId": "abc123DEF45"}},
                ]
            },
        )

    monkeypatch.setattr("app.integrations.youtube_client.requests.get", _fake_get)
    monkeypatch.setattr(client, "hydrate_video_candidates", lambda **_kwargs: [])

    results = client.fetch_search_candidates("technology news today", region_code="US")

    assert results == []
    assert captured["url"].endswith("/search")
    assert captured["headers"]["x-goog-api-key"] == "test-key"
    assert "key" not in captured["params"]
    assert quota.reserve_calls == [(100, "search")]


def test_fetch_trending_candidates_locks_out_on_quota_exceeded(monkeypatch):
    quota = _FakeQuotaBudget()
    client = YouTubeClient(channel_configs=[], quota_budget=quota)

    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")

    def _fake_get(_url, *, params=None, headers=None, timeout=None):
        assert "key" not in params
        assert headers["x-goog-api-key"] == "test-key"
        assert timeout == 10
        return _FakeResponse(
            403,
            {
                "error": {
                    "status": "PERMISSION_DENIED",
                    "errors": [{"reason": "quotaExceeded"}],
                }
            },
        )

    monkeypatch.setattr("app.integrations.youtube_client.requests.get", _fake_get)

    results = client.fetch_trending_candidates(region_code="US")

    assert results == []
    assert quota.lockout_reasons == ["quotaExceeded"]
    assert quota.reserve_calls == [(1, "general")]
