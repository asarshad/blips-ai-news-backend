from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem, ContentStatus, ContentType
from app.repositories.content_repo import ContentItemRepository
from app.services.video_surface_rules import effective_content_type, surface_content_filter


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def _make_item(
    *,
    item_type: ContentType,
    source_url: str,
    title: str,
    video_url: str | None = None,
) -> ContentItem:
    now = datetime(2026, 3, 16, 18, 0, 0)
    return ContentItem(
        type=item_type,
        source="Test Source",
        source_url=source_url,
        canonical_url=source_url,
        video_url=video_url or source_url,
        published_at=now,
        title=title,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        created_at=now,
        updated_at=now,
    )


def test_effective_content_type_treats_explicit_shorts_video_as_reel():
    item = _make_item(
        item_type=ContentType.VIDEO,
        source_url="https://www.youtube.com/shorts/VvGaDPViMKY",
        title="Shorts clip",
    )

    assert effective_content_type(item) == ContentType.REEL


def test_surface_content_filter_maps_explicit_shorts_video_to_reels():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    regular_video = _make_item(
        item_type=ContentType.VIDEO,
        source_url="https://www.youtube.com/watch?v=watch123",
        title="Regular video",
    )
    shorts_video = _make_item(
        item_type=ContentType.VIDEO,
        source_url="https://www.youtube.com/shorts/VvGaDPViMKY",
        title="Explicit Shorts URL",
    )
    native_reel = _make_item(
        item_type=ContentType.REEL,
        source_url="https://www.youtube.com/shorts/reel456",
        title="Native reel",
    )

    db.add_all([regular_video, shorts_video, native_reel])
    db.commit()

    video_titles = {
        row.title for row in db.query(ContentItem).filter(surface_content_filter("videos")).all()
    }
    reel_titles = {
        row.title for row in db.query(ContentItem).filter(surface_content_filter("reels")).all()
    }

    assert video_titles == {"Regular video"}
    assert reel_titles == {"Explicit Shorts URL", "Native reel"}


def test_get_items_for_playlist_respects_video_reel_surface_split():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    regular_video = _make_item(
        item_type=ContentType.VIDEO,
        source_url="https://www.youtube.com/watch?v=watch123",
        title="Regular video",
    )
    shorts_video = _make_item(
        item_type=ContentType.VIDEO,
        source_url="https://www.youtube.com/shorts/VvGaDPViMKY",
        title="Explicit Shorts URL",
    )
    native_reel = _make_item(
        item_type=ContentType.REEL,
        source_url="https://www.youtube.com/shorts/reel456",
        title="Native reel",
    )

    db.add_all([regular_video, shorts_video, native_reel])
    db.commit()

    repo = ContentItemRepository(db)
    video_titles = {
        row.title
        for row in repo.get_items_for_playlist(
            content_type=ContentType.VIDEO,
            hours_back=72,
            limit=50,
            ai_processed_only=False,
        )
    }
    reel_titles = {
        row.title
        for row in repo.get_items_for_playlist(
            content_type=ContentType.REEL,
            hours_back=72,
            limit=50,
            ai_processed_only=False,
        )
    }

    assert video_titles == {"Regular video"}
    assert reel_titles == {"Explicit Shorts URL", "Native reel"}
