from __future__ import annotations

from datetime import datetime, timedelta

from app.models.content import ContentType, EventType
from app.repositories.user_repo import InteractionEventRepository


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._rows)


def test_get_recent_negative_feedback_ignores_article_creator_events_and_respects_windows():
    now = datetime.utcnow()
    rows = [
        (
            EventType.VIDEO_SKIP_LT_2S,
            101,
            now - timedelta(hours=1),
            ContentType.VIDEO,
            "creator-1",
            "Video Source",
        ),
        (
            EventType.VIDEO_SKIP_LT_2S,
            202,
            now - timedelta(hours=30),
            ContentType.REEL,
            "creator-2",
            "Reel Source",
        ),
        (
            EventType.LESS_FROM_CREATOR,
            303,
            now - timedelta(hours=2),
            ContentType.REEL,
            "Creator-3",
            "Reel Source",
        ),
        (
            EventType.LESS_FROM_CREATOR,
            404,
            now - timedelta(hours=2),
            ContentType.ARTICLE,
            None,
            "Creator-3",
        ),
        (
            EventType.LESS_FROM_CREATOR,
            505,
            now - timedelta(hours=200),
            ContentType.VIDEO,
            "creator-5",
            "Video Source",
        ),
    ]
    repo = InteractionEventRepository(_FakeDB(rows))

    skipped_item_ids, creator_keys = repo.get_recent_negative_feedback(
        "device-123",
        item_hours=24,
        creator_hours=168,
        content_types=(ContentType.VIDEO, ContentType.REEL),
    )

    assert skipped_item_ids == {101}
    assert creator_keys == {"creator-3"}
