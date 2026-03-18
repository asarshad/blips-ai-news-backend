from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.integrations.youtube_channels import ChannelConfig, ChannelRole, ContentFormat, QualityTier
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.video_source import VideoSourceProfile
from app.services.video_source_service import (
    bootstrap_video_source_profiles,
    repair_video_source_metadata,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def test_bootstrap_video_source_profiles_handles_duplicate_channel_ids():
    engine = create_engine("sqlite:///:memory:")
    VideoSourceProfile.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    profiles = bootstrap_video_source_profiles(
        db,
        configs=[
            ChannelConfig(
                channel_id="channel-1",
                name="Primary Config",
                role=ChannelRole.EXPLAINER,
                content_format=ContentFormat.MIXED,
                daily_cap=2,
                quality_tier=QualityTier.PREMIUM,
                enabled=True,
            ),
            ChannelConfig(
                channel_id="channel-1",
                name="Duplicate Config",
                role=ChannelRole.SHORTS,
                content_format=ContentFormat.SHORTS,
                daily_cap=4,
                quality_tier=QualityTier.STANDARD,
                enabled=False,
            ),
        ],
    )

    rows = db.query(VideoSourceProfile).all()
    assert len(profiles) == 1
    assert len(rows) == 1
    assert rows[0].channel_name == "Primary Config"


def test_repair_video_source_metadata_backfills_curated_fields():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    VideoSourceProfile.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.VIDEO,
        source="Marques Brownlee",
        source_url="https://www.youtube.com/watch?v=abc123xyz89",
        canonical_url="https://www.youtube.com/watch?v=abc123xyz89",
        published_at=datetime(2026, 3, 13, 9, 0, 0),
        title="MacBook Air M5 Review",
        curation_status=ContentStatus.PROMOTED,
        created_at=datetime(2026, 3, 13, 9, 5, 0),
        updated_at=datetime(2026, 3, 13, 9, 5, 0),
    )
    db.add(item)
    db.commit()

    result = repair_video_source_metadata(db, lookback_days=14)
    repaired = db.get(ContentItem, item.id)

    assert result["updated"] == 1
    assert repaired.channel_id == "UCBJycsmduvYEL83R_U4JriQ"
    assert repaired.acquisition_lane == "curated"
    assert repaired.source_status == "core"


def test_repair_video_source_metadata_leaves_unmatched_rows_unchanged():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    VideoSourceProfile.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.REEL,
        source="Unknown Channel",
        source_url="https://www.youtube.com/shorts/xyz98765432",
        canonical_url="https://www.youtube.com/shorts/xyz98765432",
        published_at=datetime(2026, 3, 13, 9, 0, 0),
        title="Random Short",
        curation_status=ContentStatus.PROMOTED,
        created_at=datetime(2026, 3, 13, 9, 5, 0),
        updated_at=datetime(2026, 3, 13, 9, 5, 0),
    )
    db.add(item)
    db.commit()

    result = repair_video_source_metadata(db, lookback_days=14)
    repaired = db.get(ContentItem, item.id)

    assert result["updated"] == 0
    assert result["unresolved"] == 1
    assert repaired.channel_id is None
    assert repaired.acquisition_lane is None
    assert repaired.source_status is None


def test_repair_video_source_metadata_upserts_discovery_profiles_from_recent_items():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    VideoSourceProfile.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.REEL,
        source="Discovery Shorts Lab",
        source_url="https://www.youtube.com/shorts/xyz98765432",
        canonical_url="https://www.youtube.com/shorts/xyz98765432",
        video_url="https://www.youtube.com/shorts/xyz98765432",
        channel_id="channel-discovery-1",
        acquisition_lane="search",
        source_status="discovery",
        published_at=datetime(2026, 3, 13, 9, 0, 0),
        title="Pixel privacy shortcut",
        summary="A quick Pixel privacy shortcut demo.",
        curation_status=ContentStatus.PROMOTED,
        created_at=datetime(2026, 3, 13, 9, 5, 0),
        updated_at=datetime(2026, 3, 13, 9, 5, 0),
    )
    db.add(item)
    db.commit()

    result = repair_video_source_metadata(db, lookback_days=14)
    profile = db.get(VideoSourceProfile, "channel-discovery-1")

    assert result["profiles_upserted"] == 1
    assert profile is not None
    assert profile.channel_name == "Discovery Shorts Lab"
    assert profile.status == "discovery"
    assert profile.daily_reel_cap == 1


def test_repair_video_source_metadata_understands_query_tagged_search_provenance():
    engine = create_engine("sqlite:///:memory:")
    ContentItem.__table__.create(bind=engine)
    VideoSourceProfile.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    item = ContentItem(
        type=ContentType.VIDEO,
        source="Discovery Query Lab",
        source_url="https://www.youtube.com/watch?v=query123",
        canonical_url="https://www.youtube.com/watch?v=query123",
        video_url="https://www.youtube.com/watch?v=query123",
        channel_id="channel-discovery-query",
        discovered_via="yt_search:ai-models",
        source_status="discovery",
        published_at=datetime(2026, 3, 13, 9, 0, 0),
        title="Claude 4 agent update",
        summary="A discovery item tagged with a query-specific provenance label.",
        curation_status=ContentStatus.PROMOTED,
        created_at=datetime(2026, 3, 13, 9, 5, 0),
        updated_at=datetime(2026, 3, 13, 9, 5, 0),
    )
    db.add(item)
    db.commit()

    result = repair_video_source_metadata(db, lookback_days=14)
    repaired = db.get(ContentItem, item.id)
    profile = db.get(VideoSourceProfile, "channel-discovery-query")

    assert result["updated"] == 1
    assert result["profiles_upserted"] == 1
    assert repaired.acquisition_lane == "search"
    assert profile is not None
    assert profile.status == "discovery"
