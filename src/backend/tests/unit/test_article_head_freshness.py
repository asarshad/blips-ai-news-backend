from datetime import datetime, timezone

from app.services.article_head_freshness import prioritize_article_head


def test_prioritize_article_head_prefers_today_then_yesterday_by_quality():
    items = [
        {
            "id": 1,
            "published_at": "2026-04-10T10:00:00Z",
            "promotion_score": 0.90,
            "global_score": 0.80,
        },
        {
            "id": 2,
            "published_at": "2026-04-11T12:00:00Z",
            "promotion_score": 0.40,
            "global_score": 0.90,
        },
        {
            "id": 3,
            "published_at": "2026-04-12T09:00:00Z",
            "promotion_score": 0.20,
            "global_score": 0.20,
        },
        {
            "id": 4,
            "published_at": "2026-04-11T18:00:00Z",
            "promotion_score": 0.70,
            "global_score": 0.60,
        },
        {
            "id": 5,
            "published_at": "2026-04-12T07:00:00Z",
            "promotion_score": 0.80,
            "global_score": 0.10,
        },
    ]

    prioritized = prioritize_article_head(
        items,
        head_size=5,
        now=datetime(2026, 4, 12, 20, 0, tzinfo=timezone.utc),
    )

    assert [item["id"] for item in prioritized] == [5, 3, 4, 2, 1]


def test_prioritize_article_head_fills_remaining_slots_from_existing_order():
    items = [
        {"id": 1, "published_at": "2026-04-05T10:00:00Z", "promotion_score": 0.9},
        {"id": 2, "published_at": "2026-04-12T09:00:00Z", "promotion_score": 0.2},
        {"id": 3, "published_at": "2026-04-04T08:00:00Z", "promotion_score": 0.8},
        {"id": 4, "published_at": "2026-04-11T18:00:00Z", "promotion_score": 0.7},
        {"id": 5, "published_at": "2026-04-03T07:00:00Z", "promotion_score": 0.6},
    ]

    prioritized = prioritize_article_head(
        items,
        head_size=4,
        now=datetime(2026, 4, 12, 20, 0, tzinfo=timezone.utc),
    )

    assert [item["id"] for item in prioritized] == [2, 4, 1, 3, 5]


def test_prioritize_article_head_orders_all_days_by_recency_then_score():
    items = [
        {
            "id": 1,
            "published_at": "2026-04-08T10:00:00Z",
            "promotion_score": 0.95,
            "global_score": 0.95,
        },
        {
            "id": 2,
            "published_at": "2026-04-10T09:00:00Z",
            "promotion_score": 0.10,
            "global_score": 0.10,
        },
        {
            "id": 3,
            "published_at": "2026-04-09T12:00:00Z",
            "promotion_score": 0.90,
            "global_score": 0.90,
        },
        {
            "id": 4,
            "published_at": "2026-04-10T07:00:00Z",
            "promotion_score": 0.80,
            "global_score": 0.40,
        },
    ]

    prioritized = prioritize_article_head(
        items,
        head_size=4,
        now=datetime(2026, 4, 12, 20, 0, tzinfo=timezone.utc),
    )

    assert [item["id"] for item in prioritized] == [4, 2, 3, 1]
