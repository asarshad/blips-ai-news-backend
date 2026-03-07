from unittest.mock import MagicMock, patch

import requests

from app.ingestion.signals.discovery_feeds import fetch_discovery_leads
from app.models.signal import SignalSource


def _response_with_xml(xml_text: str) -> MagicMock:
    response = MagicMock()
    response.content = xml_text.encode("utf-8")
    response.raise_for_status.return_value = None
    return response


def test_fetch_discovery_leads_parses_feed_and_dedupes_urls():
    xml = """
    <rss version="2.0">
      <channel>
        <item><title>One</title><link>https://example.com/p/one?utm_source=tldr</link></item>
        <item><title>One dup</title><link>https://example.com/p/one?utm_medium=email</link></item>
        <item><title>Two</title><link>https://example.com/p/two</link></item>
      </channel>
    </rss>
    """

    with (
        patch(
            "app.ingestion.signals.discovery_feeds._DISCOVERY_FEED_SOURCES",
            (("Test Source", "https://example.com/feed"),),
        ),
        patch(
            "app.ingestion.signals.discovery_feeds.requests.get",
            return_value=_response_with_xml(xml),
        ),
    ):
        items = fetch_discovery_leads(limit=10, per_source_limit=10)

    assert len(items) == 2
    assert all(i.signal_source == SignalSource.DISCOVERY_LEADS for i in items)
    assert items[0].raw_url == "https://example.com/p/one"


def test_fetch_discovery_leads_honors_per_source_limit():
    xml = """
    <rss version="2.0">
      <channel>
        <item><title>A</title><link>https://example.com/p/a</link></item>
        <item><title>B</title><link>https://example.com/p/b</link></item>
        <item><title>C</title><link>https://example.com/p/c</link></item>
      </channel>
    </rss>
    """

    with (
        patch(
            "app.ingestion.signals.discovery_feeds._DISCOVERY_FEED_SOURCES",
            (("Test Source", "https://example.com/feed"),),
        ),
        patch(
            "app.ingestion.signals.discovery_feeds.requests.get",
            return_value=_response_with_xml(xml),
        ),
    ):
        items = fetch_discovery_leads(limit=10, per_source_limit=2)

    assert len(items) == 2


def test_fetch_discovery_leads_handles_request_error():
    with (
        patch(
            "app.ingestion.signals.discovery_feeds._DISCOVERY_FEED_SOURCES",
            (("Broken", "https://broken.example/feed"),),
        ),
        patch(
            "app.ingestion.signals.discovery_feeds.requests.get",
            side_effect=requests.RequestException("boom"),
        ),
    ):
        items = fetch_discovery_leads(limit=5, per_source_limit=2)

    assert items == []
