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
