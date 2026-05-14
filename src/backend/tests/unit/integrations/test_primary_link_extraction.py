"""Unit tests for Techmeme primary-link extraction (P1-1b)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.integrations.rss_client import RSSClient, _extract_primary_link_from_description
from app.integrations.rss_feeds import (
    DecayProfile,
    FeedConfig,
    FeedRole,
    QualityTier,
)

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(summary: str = "", description: str = "", link: str = "") -> SimpleNamespace:
    return SimpleNamespace(summary=summary, description=description, link=link)


# ---------------------------------------------------------------------------
# _extract_primary_link_from_description
# ---------------------------------------------------------------------------


class TestExtractPrimaryLinkFromDescription:
    def test_returns_first_external_link(self):
        """First non-techmeme <a href> is returned."""
        html = (
            '<p><a href="https://www.techmeme.com/260513/p1">Techmeme</a> &bull; '
            '<a href="https://www.wsj.com/tech/ai/story-abc">Wall Street Journal</a></p>'
        )
        entry = _make_entry(summary=html)
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result == "https://www.wsj.com/tech/ai/story-abc"

    def test_skips_techmeme_links(self):
        """All techmeme.com links are skipped."""
        html = (
            '<a href="https://techmeme.com/260513/p2">Discussion</a> '
            '<a href="https://bloomberg.com/news/articles/abc">Bloomberg</a>'
        )
        entry = _make_entry(summary=html)
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result == "https://bloomberg.com/news/articles/abc"

    def test_skips_www_techmeme_links(self):
        """www.techmeme.com links are also skipped."""
        html = (
            '<a href="https://www.techmeme.com/260513/p3">Discussion</a>'
            '<a href="https://reuters.com/technology/story">Reuters</a>'
        )
        entry = _make_entry(summary=html)
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result == "https://reuters.com/technology/story"

    def test_falls_back_to_description_attribute(self):
        """Uses entry.description when entry.summary is empty."""
        html = '<a href="https://ft.com/content/story-xyz">FT</a>'
        entry = _make_entry(summary="", description=html)
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result == "https://ft.com/content/story-xyz"

    def test_returns_none_when_no_external_links(self):
        """Returns None when only techmeme links are present."""
        html = '<a href="https://techmeme.com/260513/p4">Discussion</a>'
        entry = _make_entry(summary=html)
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result is None

    def test_returns_none_on_empty_description(self):
        """Returns None when description is empty."""
        entry = _make_entry(summary="", description="")
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result is None

    def test_skips_relative_links(self):
        """Relative hrefs are skipped; only absolute http/https links are returned."""
        html = (
            '<a href="/internal/path">Internal</a><a href="https://cnbc.com/2026/05/story">CNBC</a>'
        )
        entry = _make_entry(summary=html)
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result == "https://cnbc.com/2026/05/story"

    def test_http_links_are_accepted(self):
        """Non-HTTPS http:// links are also returned."""
        html = '<a href="http://example-news.com/story">Example</a>'
        entry = _make_entry(summary=html)
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result == "http://example-news.com/story"

    def test_first_link_wins(self):
        """When multiple external links exist, only the first is returned."""
        html = (
            '<a href="https://wsj.com/story-one">WSJ</a>'
            '<a href="https://bloomberg.com/story-two">Bloomberg</a>'
        )
        entry = _make_entry(summary=html)
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result == "https://wsj.com/story-one"

    def test_no_summary_attribute_returns_none(self):
        """Entry with no summary/description attribute at all returns None."""
        entry = SimpleNamespace()  # no summary, no description
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result is None

    def test_typical_techmeme_format(self):
        """Realistic Techmeme description HTML → WSJ original article URL."""
        html = (
            "<img src='https://www.techmeme.com/img/tmfavicon.ico' width='1' height='1' />"
            "&nbsp;(<a href='https://www.techmeme.com/260513/p42'>Techmeme</a>)&nbsp; "
            "OpenAI raises $40B "
            "(<a href='https://www.wsj.com/tech/ai/openai-raises-40-billion-valuation'>WSJ</a>)"
        )
        entry = _make_entry(summary=html)
        result = _extract_primary_link_from_description(entry, exclude_host="techmeme.com")
        assert result == "https://www.wsj.com/tech/ai/openai-raises-40-billion-valuation"


# ---------------------------------------------------------------------------
# fetch_feed with primary_link_from_description flag
# ---------------------------------------------------------------------------


_TECHMEME_FEED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Techmeme</title>
    <link>https://www.techmeme.com</link>
    <item>
      <title>OpenAI raises $40B at record valuation</title>
      <link>https://www.techmeme.com/260513/p1</link>
      <description>
        &lt;a href="https://www.techmeme.com/260513/p1"&gt;Techmeme&lt;/a&gt; &amp;bull;
        (&lt;a href="https://www.wsj.com/tech/ai/openai-raises-40-billion"&gt;Wall Street Journal&lt;/a&gt;)
        OpenAI is in talks to raise $40 billion at a record valuation according to sources.
      </description>
    </item>
  </channel>
</rss>
"""


class TestFetchFeedPrimaryLinkFlag:
    def test_primary_link_extracted_when_flag_set(self):
        """When primary_link_from_description=True, source_url comes from description."""
        client = RSSClient(feed_configs=[])
        entries = client.fetch_feed(
            _TECHMEME_FEED_XML,
            max_entries=5,
            primary_link_from_description=True,
        )
        assert len(entries) == 1
        assert entries[0].url == "https://www.wsj.com/tech/ai/openai-raises-40-billion"

    def test_entry_link_used_when_flag_not_set(self):
        """Without the flag, entry.link (Techmeme anchor page) is used as-is."""
        client = RSSClient(feed_configs=[])
        entries = client.fetch_feed(
            _TECHMEME_FEED_XML,
            max_entries=5,
            primary_link_from_description=False,
        )
        assert len(entries) == 1
        assert entries[0].url == "https://www.techmeme.com/260513/p1"

    def test_falls_back_to_entry_link_when_no_external_link(self):
        """If description has no external link, entry.link is kept."""
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Techmeme</title>
    <link>https://www.techmeme.com</link>
    <item>
      <title>Story with no external links in description</title>
      <link>https://www.techmeme.com/260513/p99</link>
      <description>No external links here at all. Just plain text.</description>
    </item>
  </channel>
</rss>
"""
        client = RSSClient(feed_configs=[])
        entries = client.fetch_feed(
            xml,
            max_entries=5,
            primary_link_from_description=True,
        )
        assert len(entries) == 1
        assert entries[0].url == "https://www.techmeme.com/260513/p99"


# ---------------------------------------------------------------------------
# fetch_all_feeds passes flag from FeedConfig
# ---------------------------------------------------------------------------


class TestFetchAllFeedsPassesFlag:
    def test_flag_propagated_from_feed_config(self, monkeypatch):
        """fetch_all_feeds passes primary_link_from_description from FeedConfig."""
        techmeme_config = FeedConfig(
            url="https://www.techmeme.com/feed.xml",
            name="Techmeme",
            role=FeedRole.MAJOR_NEWS,
            quality_tier=QualityTier.PREMIUM,
            daily_cap=5,
            decay_profile=DecayProfile.FAST,
            base_quality_weight=0.90,
            primary_link_from_description=True,
        )
        client = RSSClient(feed_configs=[techmeme_config])

        captured_kwargs: dict = {}

        def spy_fetch_feed(feed_url, max_entries=10, *, primary_link_from_description=False):
            captured_kwargs["primary_link_from_description"] = primary_link_from_description
            return []

        monkeypatch.setattr(client, "fetch_feed", spy_fetch_feed)
        client.fetch_all_feeds()

        assert captured_kwargs.get("primary_link_from_description") is True
