from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.content import ContentType
from app.services.video_duration_hydration import hydrate_missing_video_durations


def _video_item(
    item_id: int,
    *,
    source_url: str,
    video_url: str | None = None,
    duration_seconds: int | None = None,
    content_type: ContentType = ContentType.VIDEO,
):
    return SimpleNamespace(
        id=item_id,
        type=content_type,
        source="Trusted Source",
        source_url=source_url,
        video_url=video_url,
        duration_seconds=duration_seconds,
        published_at=datetime.now(timezone.utc),
    )


def test_hydrate_missing_video_durations_persists_youtube_durations():
    content_repo = MagicMock()
    youtube_client = MagicMock()
    youtube_client.get_video_duration.return_value = 611
    item = _video_item(
        1,
        source_url="https://www.youtube.com/watch?v=test1234567A",
        video_url="https://www.youtube.com/watch?v=test1234567A",
    )

    hydrated = hydrate_missing_video_durations(
        [item],
        content_repo=content_repo,
        youtube_client=youtube_client,
    )

    assert hydrated == {1: 611}
    content_repo.update_duration_seconds_bulk.assert_called_once_with({1: 611})


def test_hydrate_missing_video_durations_skips_non_youtube_and_known_durations():
    content_repo = MagicMock()
    youtube_client = MagicMock()
    items = [
        _video_item(1, source_url="https://example.com/video/1"),
        _video_item(
            2,
            source_url="https://www.youtube.com/watch?v=test1234567A",
            video_url="https://www.youtube.com/watch?v=test1234567A",
            duration_seconds=400,
        ),
        _video_item(
            3, source_url="https://example.com/article/3", content_type=ContentType.ARTICLE
        ),
    ]

    hydrated = hydrate_missing_video_durations(
        items,
        content_repo=content_repo,
        youtube_client=youtube_client,
    )

    assert hydrated == {}
    youtube_client.get_video_duration.assert_not_called()
    content_repo.update_duration_seconds_bulk.assert_not_called()
