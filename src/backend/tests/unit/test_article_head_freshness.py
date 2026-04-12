from app.services.article_head_freshness import prioritize_article_head


def test_prioritize_article_head_prefers_newer_items_within_same_tier():
    items = [
        {
            "id": 1,
            "freshness_tier": "C",
            "published_at": "2026-04-05T10:00:00",
            "created_at": "2026-04-05T10:05:00",
        },
        {
            "id": 2,
            "freshness_tier": "C",
            "published_at": "2026-04-08T10:00:00",
            "created_at": "2026-04-08T10:05:00",
        },
        {
            "id": 3,
            "freshness_tier": "A",
            "published_at": "2026-04-11T10:00:00",
            "created_at": "2026-04-11T10:05:00",
        },
        {
            "id": 4,
            "freshness_tier": "B",
            "published_at": "2026-04-07T10:00:00",
            "created_at": "2026-04-10T10:05:00",
        },
    ]

    prioritized = prioritize_article_head(items, head_size=4)

    assert [item["id"] for item in prioritized] == [3, 4, 2, 1]
