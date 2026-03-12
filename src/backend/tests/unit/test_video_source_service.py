from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.integrations.youtube_channels import ChannelConfig, ChannelRole, ContentFormat, QualityTier
from app.models.video_source import VideoSourceProfile
from app.services.video_source_service import bootstrap_video_source_profiles


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
