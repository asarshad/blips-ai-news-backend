import pytest

from app.ingestion.url_normalizer import normalize_url

pytestmark = [pytest.mark.unit]


def test_normalize_url_strips_tracking_and_fragments():
    url = "https://Example.com/path/to/article/?utm_source=rss&utm_medium=feed&ref=homepage#section"
    normalized = normalize_url(url)

    assert normalized == "https://example.com/path/to/article"


def test_normalize_url_keeps_non_tracking_query_params():
    url = "https://example.com/article?id=123&utm_source=rss"
    assert normalize_url(url) == "https://example.com/article?id=123"


def test_normalize_url_canonicalizes_youtube_watch_and_shorts():
    assert normalize_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=43") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert normalize_url("https://youtu.be/dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert normalize_url("https://www.youtube.com/shorts/abc123DEF45?feature=share") == "https://www.youtube.com/shorts/abc123DEF45"
