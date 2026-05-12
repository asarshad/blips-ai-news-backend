from datetime import datetime, timezone
from unittest.mock import Mock

from app.models.content import ContentItem, ContentType
from app.services.playlist_service import (
    CATEGORY_CAP_WINDOW_SIZE,
    MAX_PER_SOURCE_PER_PLAYLIST,
    MAX_SOURCE_PER_WINDOW,
    SOURCE_CAP_WINDOW_SIZE,
    PlaylistService,
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


def test_global_source_cap_hard_limit():
    """No source should appear more than MAX_PER_SOURCE_PER_PLAYLIST times."""
    service = _service()
    # 10 items from source-a, each with distinct cluster and topic
    scored_candidates = [
        (_item(item_id=i, source="source-a", topic=f"topic-{i}"), 1.0 - i * 0.01)
        for i in range(1, 11)
    ] + [
        (_item(item_id=100 + i, source="source-b", topic=f"topic-b-{i}"), 0.5 - i * 0.01)
        for i in range(1, 6)
    ]

    selected = service._select_diverse_items(scored_candidates, size=15)

    source_a_count = sum(1 for item in selected if item.source.lower() == "source-a")
    assert source_a_count <= MAX_PER_SOURCE_PER_PLAYLIST


def test_global_source_cap_allows_multiple_sources_up_to_limit():
    """Each of N sources should be allowed up to the cap."""
    service = _service()
    # 5 items from each of 4 sources
    scored_candidates = []
    for src_idx in range(4):
        for item_idx in range(5):
            iid = src_idx * 10 + item_idx + 1
            scored_candidates.append(
                (_item(item_id=iid, source=f"source-{src_idx}", topic=f"topic-{iid}"), 1.0 / iid)
            )

    selected = service._select_diverse_items(scored_candidates, size=20)

    from collections import Counter

    counts = Counter(item.source.lower() for item in selected)
    for source, count in counts.items():
        assert count <= MAX_PER_SOURCE_PER_PLAYLIST, f"{source} appeared {count} times"


def test_global_source_cap_does_not_prevent_filling_playlist():
    """Playlist fills to requested size when enough distinct sources exist."""
    service = _service()
    # 20 sources × 4 items each = 80 candidates; playlist of 20 should fill
    scored_candidates = []
    for src_idx in range(20):
        for item_idx in range(4):
            iid = src_idx * 10 + item_idx + 1
            scored_candidates.append(
                (_item(item_id=iid, source=f"source-{src_idx}", topic=f"topic-{iid}"), 1.0 / iid)
            )

    selected = service._select_diverse_items(scored_candidates, size=20)

    assert len(selected) == 20
