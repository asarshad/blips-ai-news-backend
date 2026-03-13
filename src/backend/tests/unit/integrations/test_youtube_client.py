import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.integrations.youtube_channels import (
    ChannelConfig,
    ChannelRole,
    ContentFormat,
    QualityTier,
)
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
    hydrated = {}

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
    monkeypatch.setattr(
        client,
        "hydrate_video_candidates",
        lambda **kwargs: hydrated.update(kwargs) or [],
    )

    results = client.fetch_search_candidates(
        "technology news",
        region_code="US",
        search_order="viewCount",
        query_label="story-google-maps",
    )

    assert results == []
    assert captured["url"].endswith("/search")
    assert captured["headers"]["x-goog-api-key"] == "test-key"
    assert "key" not in captured["params"]
    assert captured["params"]["order"] == "viewCount"
    assert hydrated["query_label"] == "story-google-maps"
    assert quota.reserve_calls == [(100, "search")]


def test_guess_role_requires_official_channel_name_instead_of_topic_terms():
    client = YouTubeClient(channel_configs=[])

    assert (
        client._guess_role(
            "Random Tech Daily",
            "Apple launches new iPhone",
            "Roundup of the keynote highlights.",
            "videos",
        )
        != ChannelRole.OFFICIAL
    )
    assert (
        client._guess_role(
            "Apple",
            "Spring Event keynote",
            "Official launch stream",
            "videos",
        )
        == ChannelRole.OFFICIAL
    )


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


def test_fetch_trending_candidates_uses_single_api_round_trip(monkeypatch):
    quota = _FakeQuotaBudget()
    client = YouTubeClient(channel_configs=[], quota_budget=quota)

    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")

    def _fake_get(_url, *, params=None, headers=None, timeout=None):
        assert "key" not in params
        assert params["part"] == "snippet,contentDetails,statistics,status"
        assert headers["x-goog-api-key"] == "test-key"
        assert timeout == 10
        return _FakeResponse(
            200,
            {
                "items": [
                    {
                        "id": "abc123DEF45",
                        "snippet": {
                            "title": "Daily tech news roundup",
                            "description": "Top stories in tech today.",
                            "channelId": "channel-1",
                            "channelTitle": "Tech News",
                            "publishedAt": "2026-03-12T12:00:00Z",
                            "defaultLanguage": "en",
                        },
                        "contentDetails": {"duration": "PT8M"},
                        "statistics": {"viewCount": "1000", "likeCount": "25"},
                        "status": {"uploadStatus": "processed"},
                    }
                ]
            },
        )

    monkeypatch.setattr("app.integrations.youtube_client.requests.get", _fake_get)
    monkeypatch.setattr(
        client,
        "hydrate_video_candidates",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("hydrate should not run")),
    )

    results = client.fetch_trending_candidates(region_code="US", surface="videos")

    assert len(results) == 1
    assert results[0].video_id == "abc123DEF45"
    assert results[0].source == "Tech News"
    assert results[0].is_short is False
    assert quota.reserve_calls == [(1, "general")]


def test_fetch_channel_stats_batches_channel_requests(monkeypatch):
    quota = _FakeQuotaBudget()
    client = YouTubeClient(channel_configs=[], quota_budget=quota)

    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")

    def _fake_get(_url, *, params=None, headers=None, timeout=None):
        assert params["part"] == "statistics"
        assert params["id"] == "channel-1,channel-2"
        assert headers["x-goog-api-key"] == "test-key"
        assert timeout == 10
        return _FakeResponse(
            200,
            {
                "items": [
                    {
                        "id": "channel-1",
                        "statistics": {
                            "subscriberCount": "12000",
                            "videoCount": "320",
                            "viewCount": "5000000",
                        },
                    },
                    {
                        "id": "channel-2",
                        "statistics": {
                            "subscriberCount": "45000",
                            "videoCount": "910",
                            "viewCount": "12000000",
                        },
                    },
                ]
            },
        )

    monkeypatch.setattr("app.integrations.youtube_client.requests.get", _fake_get)

    stats = client.fetch_channel_stats(["channel-1", "channel-2", "channel-1"])

    assert stats["channel-1"]["subscriber_count"] == 12000
    assert stats["channel-1"]["video_count"] == 320
    assert stats["channel-2"]["subscriber_count"] == 45000
    assert quota.reserve_calls == [(1, "general")]


def test_fetch_channel_with_mixed_format_batches_duration_lookups(monkeypatch):
    quota = _FakeQuotaBudget()
    client = YouTubeClient(channel_configs=[], quota_budget=quota)
    config = ChannelConfig(
        channel_id="mixed-channel-1",
        name="Mixed Channel",
        role=ChannelRole.EXPLAINER,
        content_format=ContentFormat.MIXED,
        quality_tier=QualityTier.STANDARD,
        daily_cap=2,
    )

    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setattr(
        client,
        "_parse_feed_with_retries",
        lambda _feed_url: SimpleNamespace(
            bozo=False,
            feed={"title": "Mixed Channel"},
            entries=[
                {
                    "link": "https://www.youtube.com/watch?v=short12345A",
                    "title": "Quick tip",
                },
                {
                    "link": "https://www.youtube.com/watch?v=long12345AB",
                    "title": "Deep dive",
                },
            ],
        ),
    )

    calls = []

    def _fake_api_json(
        endpoint, *, params, quota_units, quota_bucket="general", timeout=10.0, operation
    ):
        calls.append((endpoint, params["id"], quota_units, quota_bucket, operation))
        return {
            "items": [
                {"id": "short12345A", "contentDetails": {"duration": "PT30S"}},
                {"id": "long12345AB", "contentDetails": {"duration": "PT10M"}},
            ]
        }

    monkeypatch.setattr(client, "_youtube_api_json", _fake_api_json)

    videos = client._fetch_channel_with_config(config, 2)

    assert len(calls) == 1
    assert calls[0][:4] == (
        "videos",
        "short12345A,long12345AB",
        1,
        "duration",
    )
    assert [video.is_short for video in videos] == [True, False]
    assert [video.duration_seconds for video in videos] == [30, 600]
    assert [video.source_status for video in videos] == ["core", "core"]
    assert videos[0].acquisition_lane == "curated"
    assert videos[0].format_fit_score == 1.0
    assert videos[1].format_fit_score == 1.0
