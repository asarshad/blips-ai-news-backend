from app.ingestion.canonical import (
    canonical_key_for_article,
    canonical_key_for_youtube,
    extract_youtube_video_id,
)


def test_extract_youtube_video_id_handles_watch_and_shorts_and_youtu_be():
    assert (
        extract_youtube_video_id("https://www.youtube.com/watch?v=abc123&feature=youtu.be")
        == "abc123"
    )
    assert extract_youtube_video_id("https://www.youtube.com/shorts/xyz789?si=1") == "xyz789"
    assert extract_youtube_video_id("https://youtu.be/qqq111?t=5") == "qqq111"


def test_canonical_key_for_article_hashes_normalized_url():
    k1 = canonical_key_for_article(
        canonical_url="https://Example.com/a/?utm_source=x#frag", source_url=""
    )
    k2 = canonical_key_for_article(
        canonical_url="https://example.com/a/?utm_source=y", source_url=""
    )
    assert k1 == k2
    assert len(k1) == 32


def test_canonical_key_for_youtube_prefers_video_id():
    k = canonical_key_for_youtube(
        video_id="abc123", source_url="https://www.youtube.com/watch?v=abc123", video_url=None
    )
    assert k == "abc123"

    k2 = canonical_key_for_youtube(
        video_id=None,
        source_url="https://www.youtube.com/watch?v=abc123&utm_source=x",
        video_url=None,
    )
    assert k2 == "abc123"
