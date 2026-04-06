import json
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentStatus, ContentType
from app.services.playlist_service import refresh_cached_playlist_items


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


class _MemoryRedis:
    def __init__(self):
        self._values = {}
        self._ttl = {}

    def get(self, key):
        return self._values.get(key)

    def setex(self, key, ttl, value):
        self._values[key] = value
        self._ttl[key] = ttl

    def keys(self, pattern):
        if pattern == "playlist:*:ARTICLE:*":
            return [
                key
                for key in self._values
                if key.startswith("playlist:") and ":ARTICLE:" in key
            ]
        if pattern == "playlist:video-reel-v4:*:VIDEO:*":
            return [
                key
                for key in self._values
                if key.startswith("playlist:video-reel-v4:") and ":VIDEO:" in key
            ]
        return []

    def ttl(self, key):
        return self._ttl.get(key, 300)


def test_refresh_cached_playlist_items_rewrites_matching_article_snapshots():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        id=294886,
        type=ContentType.ARTICLE,
        source="Engadget",
        source_url="https://www.engadget.com/story",
        canonical_url="https://www.engadget.com/story",
        title="The Backrooms trailer combines creepypasta dread and A24 prestige horror",
        summary="Fresh summary",
        image_url="https://cdn.example.com/correct.jpg",
        article_image_status="VERIFIED",
        ai_processed=True,
        curation_status=ContentStatus.PROMOTED,
        published_at=datetime(2026, 4, 2, 3, 0, 0),
        created_at=datetime(2026, 4, 2, 3, 5, 0),
        updated_at=datetime(2026, 4, 2, 4, 0, 0),
    )
    db.add(item)
    db.commit()

    redis = _MemoryRedis()
    redis.setex(
        "playlist:session:abc123:ARTICLE:session-1",
        3600,
        json.dumps(
            {
                "generated_at": "2026-04-02T03:10:00",
                "feed_version": "old-version",
                "items": [
                    {
                        "id": 294886,
                        "type": "ARTICLE",
                        "title": "Old title",
                        "source": "Engadget",
                        "source_url": "https://www.engadget.com/story",
                        "summary": "Old summary",
                        "image_url": "https://cdn.example.com/old.jpg",
                        "published_at": "2026-04-02T03:00:00",
                        "created_at": "2026-04-02T03:05:00",
                        "updated_at": "2026-04-02T03:05:00",
                        "freshness_tier": "A",
                        "freshness_reason": "fresh_published",
                    }
                ],
            }
        ),
    )

    result = refresh_cached_playlist_items(db, content_ids=[294886], redis_client=redis)

    assert result["cache_keys_scanned"] == 1
    assert result["cache_keys_updated"] == 1
    assert result["cached_items_updated"] == 1

    payload = json.loads(redis.get("playlist:session:abc123:ARTICLE:session-1"))
    assert payload["feed_version"] != "old-version"
    assert payload["items"][0]["image_url"] == "https://cdn.example.com/correct.jpg"
    assert payload["items"][0]["summary"] == "Fresh summary"
    assert payload["items"][0]["updated_at"] == "2026-04-02T04:00:00"


def test_refresh_cached_playlist_items_removes_unready_video_snapshots():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        id=77,
        type=ContentType.VIDEO,
        source="CNET",
        source_url="https://www.youtube.com/watch?v=moon123",
        title="NASA's Artemis II Moon Flyby",
        summary=None,
        image_url="https://img.youtube.com/vi/moon123/maxresdefault.jpg",
        video_url="https://www.youtube.com/watch?v=moon123",
        curation_status=ContentStatus.PROMOTED,
        ai_processed=False,
        published_at=datetime(2026, 4, 2, 3, 0, 0),
        created_at=datetime(2026, 4, 2, 3, 5, 0),
        updated_at=datetime(2026, 4, 2, 4, 0, 0),
    )
    db.add(item)
    db.commit()

    redis = _MemoryRedis()
    redis.setex(
        "playlist:video-reel-v4:session:abc123:VIDEO:session-1",
        3600,
        json.dumps(
            {
                "generated_at": "2026-04-02T03:10:00",
                "feed_version": "old-version",
                "items": [
                    {
                        "id": 77,
                        "type": "VIDEO",
                        "title": "NASA's Artemis II Moon Flyby",
                        "source": "CNET",
                        "source_url": "https://www.youtube.com/watch?v=moon123",
                        "summary": "Watch all the drama and excitement...",
                        "image_url": "https://img.youtube.com/vi/moon123/maxresdefault.jpg",
                        "video_url": "https://www.youtube.com/watch?v=moon123",
                        "published_at": "2026-04-02T03:00:00",
                        "created_at": "2026-04-02T03:05:00",
                        "updated_at": "2026-04-02T03:05:00",
                        "freshness_tier": "A",
                        "freshness_reason": "fresh_published",
                    }
                ],
            }
        ),
    )

    result = refresh_cached_playlist_items(db, content_ids=[77], redis_client=redis)

    assert result["cache_keys_scanned"] == 1
    assert result["cache_keys_updated"] == 1
    assert result["cached_items_updated"] == 1

    payload = json.loads(redis.get("playlist:video-reel-v4:session:abc123:VIDEO:session-1"))
    assert payload["items"] == []
