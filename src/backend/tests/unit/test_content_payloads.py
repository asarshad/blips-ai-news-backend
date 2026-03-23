from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models.content import ContentType
from app.services.content_payloads import (
    content_item_to_article_payload,
    content_item_to_video_payload,
)


def test_article_payload_includes_feed_card_and_notification_fields():
    now = datetime(2026, 3, 23, 12, 0, 0)
    item = SimpleNamespace(
        id=100,
        type=ContentType.ARTICLE,
        title="Big launch roundup",
        canonical_url="https://example.com/articles/launch",
        source_url="https://example.com/articles/launch?ref=rss",
        source="Example News",
        summary="A condensed summary of the major launch announcements.",
        image_url="https://example.com/image.jpg",
        published_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=1),
        topics=["AI", "Mobile"],
        conversation_starters={"starters": ["What changed?", "Why now?"]},
    )

    payload = content_item_to_article_payload(item, now=now)

    assert payload["id"] == 100
    assert payload["type"] == "ARTICLE"
    assert payload["source"] == "Example News"
    assert payload["published_at"] == item.published_at
    assert payload["freshness_tier"] in {"A", "B", "C"}
    assert payload["freshness_reason"] in {"fresh_published", "recently_added", "evergreen"}
    assert isinstance(payload["published_age_seconds"], int)
    assert isinstance(payload["added_age_seconds"], int)
    assert payload["conversation_starters"] == {"starters": ["What changed?", "Why now?"]}


def test_video_payload_preserves_reel_type_and_starters():
    now = datetime(2026, 3, 23, 12, 0, 0)
    item = SimpleNamespace(
        id=200,
        type=ContentType.REEL,
        title="Quick gadget demo",
        summary="A short hands-on reel.",
        video_url="https://www.youtube.com/shorts/demo123",
        source_url="https://www.youtube.com/shorts/demo123",
        image_url="https://img.youtube.com/demo123.jpg",
        source="YouTube",
        topics=["Gadgets"],
        duration_seconds=45,
        global_score=0.42,
        created_at=now - timedelta(hours=4),
        published_at=now - timedelta(hours=3),
        conversation_starters={"starters": ["Worth buying?"]},
    )

    payload = content_item_to_video_payload(item, now=now)

    assert payload["id"] == 200
    assert payload["type"] == "REEL"
    assert payload["video_url"] == "https://www.youtube.com/shorts/demo123"
    assert payload["freshness_tier"] in {"A", "B", "C"}
    assert payload["published_age_seconds"] >= 0
    assert payload["added_age_seconds"] >= 0
    assert payload["conversation_starters"] == {"starters": ["Worth buying?"]}
