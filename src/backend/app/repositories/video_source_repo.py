"""Repositories for video source governance and discovery metrics."""

from datetime import datetime, timedelta
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from app.integrations.youtube_channels import ChannelConfig, ContentFormat
from app.models.video_source import VideoDiscoveryRun, VideoSourceProfile


class VideoSourceProfileRepository:
    """Persistence helpers for video source profiles."""

    def __init__(self, db: Session):
        self.db = db

    def get(self, channel_id: str) -> Optional[VideoSourceProfile]:
        return (
            self.db.query(VideoSourceProfile)
            .filter(VideoSourceProfile.channel_id == channel_id)
            .first()
        )

    def list_all(self) -> List[VideoSourceProfile]:
        return (
            self.db.query(VideoSourceProfile)
            .order_by(VideoSourceProfile.status, VideoSourceProfile.channel_name)
            .all()
        )

    def upsert_from_registry(self, configs: Iterable[ChannelConfig]) -> List[VideoSourceProfile]:
        """Ensure every curated channel has a persisted governance profile."""
        profiles: List[VideoSourceProfile] = []
        for config in configs:
            profile = self.get(config.channel_id)
            reel_cap = config.daily_cap if config.content_format != ContentFormat.LONG_FORM else 1
            if profile is None:
                profile = VideoSourceProfile(
                    channel_id=config.channel_id,
                    channel_name=config.name,
                    role=config.role.value,
                    content_format=config.content_format.value,
                    quality_tier=config.quality_tier.value,
                    status="core" if config.enabled else "blocked",
                    enabled=config.enabled,
                    curated_seed=True,
                    daily_video_cap=config.daily_cap,
                    daily_reel_cap=reel_cap,
                    allow_curated=True,
                    allow_search=True,
                    allow_trending=True,
                )
                self.db.add(profile)
            else:
                profile.channel_name = config.name
                profile.role = config.role.value
                profile.content_format = config.content_format.value
                profile.quality_tier = config.quality_tier.value
                profile.enabled = config.enabled
                profile.curated_seed = True
                profile.daily_video_cap = config.daily_cap
                profile.daily_reel_cap = reel_cap
                if not config.enabled:
                    profile.status = "blocked"
            profiles.append(profile)
        self.db.flush()
        return profiles

    def record_run(self, **kwargs) -> VideoDiscoveryRun:
        run = VideoDiscoveryRun(**kwargs)
        self.db.add(run)
        self.db.flush()
        return run

    def recent_runs(self, hours_back: int = 168) -> List[VideoDiscoveryRun]:
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        return (
            self.db.query(VideoDiscoveryRun)
            .filter(VideoDiscoveryRun.run_started_at >= cutoff)
            .all()
        )
