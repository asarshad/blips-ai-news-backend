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
