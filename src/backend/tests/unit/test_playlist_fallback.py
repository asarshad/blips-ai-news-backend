import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.content import ContentType
from app.services.playlist_service import (
    FALLBACK_CONTENT_AGE_HOURS,
    MAX_CONTENT_AGE_HOURS,
    PlaylistService,
)


def _item(item_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        type=ContentType.ARTICLE,
        source="TechCrunch",
        source_url=f"https://example.com/{item_id}",
        title=f"Item {item_id}",
        description="desc",
        summary="summary",
        image_url=None,
        video_url=None,
        duration_seconds=None,
        topics=["AI"],
        entities=[],
        published_at=datetime.now(timezone.utc),
        global_score=0.8,
        cluster_id=f"c{item_id}",
        editorial_boost=0,
    )


def test_get_playlist_falls_back_to_cached_snapshot_when_no_new_candidates():
    content_repo = MagicMock()
    content_repo.get_items_for_playlist.return_value = []

    personalization = MagicMock()
    personalization.compute_personalization_score.return_value = 0.1

    redis = MagicMock()
    redis.get.side_effect = [
        None,  # miss on session cache
        json.dumps([{"id": 99, "type": "ARTICLE"}]),  # hit on latest fallback cache
    ]

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        redis_client=redis,
    )

    result = service.get_playlist("device-1", ContentType.ARTICLE, size=20)

    assert result["items"] == [{"id": 99, "type": "ARTICLE"}]
    assert result["total_items"] == 1
    assert result["has_more"] is False
    assert redis.setex.called


def test_get_playlist_expands_to_historical_window_when_recent_inventory_empty():
    recent_call = []

    def _get_items_for_playlist(*, content_type, hours_back, limit):
        recent_call.append(hours_back)
        if hours_back == MAX_CONTENT_AGE_HOURS:
            return []
        if hours_back == FALLBACK_CONTENT_AGE_HOURS:
            return [_item(10)]
        return []

    content_repo = MagicMock()
    content_repo.get_items_for_playlist.side_effect = _get_items_for_playlist

    personalization = MagicMock()
    personalization.compute_personalization_score.return_value = 0.3

    service = PlaylistService(
        content_repo=content_repo,
        profile_repo=MagicMock(),
        preference_repo=MagicMock(),
        personalization_service=personalization,
        redis_client=None,
    )

    result = service.get_playlist("device-2", ContentType.ARTICLE, size=20)

    assert recent_call[0] == MAX_CONTENT_AGE_HOURS
    assert recent_call[1] == FALLBACK_CONTENT_AGE_HOURS
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == 10
