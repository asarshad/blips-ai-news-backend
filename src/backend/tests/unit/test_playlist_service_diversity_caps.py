from datetime import datetime, timezone
from unittest.mock import Mock

from app.models.content import ContentItem, ContentType
from app.services.playlist_service import (
    CATEGORY_CAP_WINDOW_SIZE,
    MAX_SOURCE_PER_WINDOW,
    PlaylistService,
    SOURCE_CAP_WINDOW_SIZE,
)


def _service() -> PlaylistService:
    personalization = Mock()
    return PlaylistService(
        content_repo=Mock(),
        profile_repo=Mock(),
        preference_repo=Mock(),
        personalization_service=personalization,
        redis_client=None,
    )


def _item(
    *,
    item_id: int,
    source: str,
    topic: str,
) -> ContentItem:
    return ContentItem(
        id=item_id,
        type=ContentType.ARTICLE,
        title=f"Item {item_id}",
        source=source,
        source_url=f"https://example.com/{item_id}",
        published_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        topics=[topic],
        cluster_id=f"cluster-{item_id}",
    )


def test_select_diverse_items_enforces_rolling_source_cap():
    service = _service()
    scored_candidates = [
        (_item(item_id=1, source="source-a", topic="security"), 0.99),
        (_item(item_id=2, source="source-a", topic="cloud"), 0.98),
        (_item(item_id=3, source="source-a", topic="infra"), 0.97),
        (_item(item_id=4, source="source-b", topic="security"), 0.96),
        (_item(item_id=5, source="source-c", topic="cloud"), 0.95),
        (_item(item_id=6, source="source-d", topic="infra"), 0.94),
    ]

    selected = service._select_diverse_items(scored_candidates, size=6)

    sources = [item.source.lower() for item in selected]
    for index in range(max(0, len(sources) - SOURCE_CAP_WINDOW_SIZE + 1)):
        window = sources[index : index + SOURCE_CAP_WINDOW_SIZE]
        for source in set(window):
            assert window.count(source) <= MAX_SOURCE_PER_WINDOW


def test_select_diverse_items_enforces_category_cap():
    service = _service()
    scored_candidates = [
        (_item(item_id=1, source="source-a", topic="AI"), 0.99),
        (_item(item_id=2, source="source-b", topic="AI"), 0.98),
        (_item(item_id=3, source="source-c", topic="AI"), 0.97),
        (_item(item_id=4, source="source-d", topic="AI"), 0.96),
        (_item(item_id=5, source="source-e", topic="Security"), 0.95),
        (_item(item_id=6, source="source-f", topic="Cloud"), 0.94),
        (_item(item_id=7, source="source-g", topic="Data"), 0.93),
    ]

    selected = service._select_diverse_items(scored_candidates, size=7)

    topics = [(item.topics or [""])[0].lower() for item in selected]
    for index in range(max(0, len(topics) - CATEGORY_CAP_WINDOW_SIZE + 1)):
        window = topics[index : index + CATEGORY_CAP_WINDOW_SIZE]
        assert window.count("ai") <= 2
