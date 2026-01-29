import datetime as dt

import pytest
import requests_mock

from app.integrations.rss_client import RSSClient, decode_html_entities

pytestmark = [pytest.mark.unit]


def test_decode_html_entities_handles_numeric_and_named_entities():
    assert decode_html_entities("AI&#8217;s next leap &amp; what it means") == "AI’s next leap & what it means"


def test_fetch_feed_parses_fixture_xml_without_network(fixture_text, monkeypatch):
    xml = fixture_text("rss/sample_feed.xml")
    client = RSSClient(feed_configs=[])

    # Avoid network article extraction; still validates feedparser parsing and title decode.
    monkeypatch.setattr(client, "_extract_article_content", lambda url: "extracted content")
    monkeypatch.setattr(client, "_fetch_og_image", lambda url: "")

    entries = client.fetch_feed(xml, max_entries=10)

    # With extraction monkeypatched to always succeed, both items should be returned.
    assert len(entries) == 2
    assert entries[0].title == "AI’s next leap & what it means"
    assert entries[0].url.startswith("https://example.com/articles/ai-next")
    assert isinstance(entries[0].published_date, dt.datetime)


def test_extract_article_content_from_html_fixture_without_real_http(fixture_text):
    url = "https://example.com/articles/ai-next"
    html = fixture_text("html/article_simple.html")

    client = RSSClient(feed_configs=[])

    with requests_mock.Mocker() as m:
        m.get(url, text=html, status_code=200)
        content = client._extract_article_content(url)

    assert "This is a real paragraph" in content
    assert "Another paragraph" in content
    assert len(content) > 50
