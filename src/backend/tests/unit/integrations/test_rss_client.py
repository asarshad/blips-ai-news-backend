import datetime as dt

import pytest

from app.integrations.rss_client import RSSClient, decode_html_entities

pytestmark = [pytest.mark.unit]


def test_decode_html_entities_handles_numeric_and_named_entities():
    assert (
        decode_html_entities("AI&#8217;s next leap &amp; what it means")
        == "AI\u2019s next leap & what it means"
    )


def test_fetch_feed_parses_fixture_xml_without_network(fixture_text):
    xml = fixture_text("rss/sample_feed.xml")
    client = RSSClient(feed_configs=[])

    entries = client.fetch_feed(xml, max_entries=10)

    # Only the first item has a usable <description>; the second is empty
    # and gets skipped in RSS-only mode (no scraping fallback).
    assert len(entries) == 1
    assert entries[0].title == "AI\u2019s next leap & what it means"
    assert entries[0].url.startswith("https://example.com/articles/ai-next")
    assert isinstance(entries[0].published_date, dt.datetime)


def test_rss_only_mode_skips_articles_without_description():
    """Articles with empty RSS description are skipped."""
    client = RSSClient(feed_configs=[])

    class FakeEntry:
        content = None
        summary = ""
        description = ""

    result = client._get_rss_description(FakeEntry())
    assert result == ""
