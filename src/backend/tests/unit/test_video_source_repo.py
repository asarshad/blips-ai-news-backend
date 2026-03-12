from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.integrations.youtube_channels import ChannelConfig, ChannelRole, ContentFormat, QualityTier
from app.models.video_source import VideoSourceProfile
from app.repositories.video_source_repo import VideoSourceProfileRepository


def test_upsert_from_registry_dedupes_duplicate_channel_ids():
    engine = create_engine("sqlite:///:memory:")
    VideoSourceProfile.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    repo = VideoSourceProfileRepository(db)
    profiles = repo.upsert_from_registry(
        [
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
                name="Disabled Shorts Duplicate",
                role=ChannelRole.SHORTS,
                content_format=ContentFormat.SHORTS,
                daily_cap=4,
                quality_tier=QualityTier.STANDARD,
                enabled=False,
            ),
        ]
    )
    db.commit()

    rows = db.query(VideoSourceProfile).all()
    assert len(profiles) == 1
    assert len(rows) == 1
    assert rows[0].channel_name == "Primary Config"
    assert rows[0].content_format == ContentFormat.MIXED.value
    assert rows[0].status == "core"
