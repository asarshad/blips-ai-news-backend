import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_fetch_feed_retries_then_succeeds(monkeypatch, fixture_text):
    xml = fixture_text("rss/sample_feed.xml").encode("utf-8")
    client = RSSClient(feed_configs=[])
    monkeypatch.setenv("CONNECTOR_MAX_RETRIES", "2")
    monkeypatch.setenv("CONNECTOR_RETRY_BUDGET", "2")
    monkeypatch.setattr(client, "_timeout_seconds", 1.0)
    monkeypatch.setattr(client, "_max_retries", 2)
    monkeypatch.setattr(client, "_retry_budget_remaining", 2)
    monkeypatch.setattr(client, "_backoff_base_seconds", 0.0)

    calls = {"n": 0}

    import requests

    # First call raises generic Exception, so use RequestException to match codepath.
    def _fake_get_request_exc(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.RequestException("transient")
        resp = Mock()
        resp.content = xml
        resp.raise_for_status.return_value = None
        return resp

    monkeypatch.setattr(requests, "get", _fake_get_request_exc)
    entries = client.fetch_feed("https://example.com/feed.xml", max_entries=10)

    assert calls["n"] == 2
    assert len(entries) == 1


def test_fetch_feed_stops_when_retry_budget_exhausted(monkeypatch):
    client = RSSClient(feed_configs=[])
    monkeypatch.setattr(client, "_timeout_seconds", 1.0)
    monkeypatch.setattr(client, "_max_retries", 3)
    monkeypatch.setattr(client, "_retry_budget_remaining", 0)
    monkeypatch.setattr(client, "_backoff_base_seconds", 0.0)

    import requests

    def _always_fail(*_args, **_kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "get", _always_fail)
    entries = client.fetch_feed("https://example.com/feed.xml", max_entries=10)
    assert entries == []


def test_extract_image_url_uses_rss_summary_image_without_page_fallback(monkeypatch):
    client = RSSClient(feed_configs=[])
    entry = SimpleNamespace(
        media_content=[],
        media_thumbnail=[],
        enclosures=[],
        summary='<p><img src="/images/hero.jpg" /></p>',
    )

    def _should_not_run(_url):
        raise AssertionError("page metadata fallback should not run when RSS image exists")

    monkeypatch.setattr(client, "_extract_image_from_page_metadata", _should_not_run)

    image_url = client._extract_image_url(entry, "https://example.com/story/1")
    assert image_url == "https://example.com/images/hero.jpg"


def test_extract_image_url_prefers_editorial_summary_image_over_logo(monkeypatch):
    client = RSSClient(feed_configs=[])
    entry = SimpleNamespace(
        media_content=[],
        media_thumbnail=[],
        enclosures=[],
        summary=(
            '<p><img src="/assets/logo.png" width="96" height="96" alt="Site logo" /></p>'
            '<p><img src="/images/hero.jpg" width="1280" height="720" alt="Feature image" /></p>'
        ),
    )

    def _should_not_run(_url):
        raise AssertionError("page metadata fallback should not run when RSS hero image exists")

    monkeypatch.setattr(client, "_extract_image_from_page_metadata", _should_not_run)

    image_url = client._extract_image_url(entry, "https://example.com/story/logo-first")
    assert image_url == "https://example.com/images/hero.jpg"


def test_extract_image_url_falls_back_to_page_metadata(monkeypatch):
    client = RSSClient(feed_configs=[])
    entry = SimpleNamespace(media_content=[], media_thumbnail=[], enclosures=[], summary="")
    monkeypatch.setattr(
        client,
        "_extract_image_from_page_metadata",
        lambda _url: "https://cdn.example.com/og.jpg",
    )

    image_url = client._extract_image_url(entry, "https://example.com/story/2")
    assert image_url == "https://cdn.example.com/og.jpg"


def test_extract_image_url_replaces_generic_rss_candidate_with_page_metadata(monkeypatch):
    client = RSSClient(feed_configs=[])
    entry = SimpleNamespace(
        media_content=[{"url": "https://cdn.example.com/social-share.png"}],
        media_thumbnail=[],
        enclosures=[],
        summary="",
    )
    monkeypatch.setattr(
        client,
        "_extract_image_from_page_metadata",
        lambda _url: "https://example.com/images/article-hero.jpg",
    )

    image_url = client._extract_image_url(entry, "https://example.com/story/3")
    assert image_url == "https://example.com/images/article-hero.jpg"
