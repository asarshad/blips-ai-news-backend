"""Repositories for video source governance and discovery metrics."""

from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.integrations.youtube_channels import ChannelConfig, dedupe_channel_configs
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

    def get_many(self, channel_ids: Iterable[str]) -> Dict[str, VideoSourceProfile]:
        normalized = [channel_id for channel_id in dict.fromkeys(channel_ids) if channel_id]
        if not normalized:
            return {}
        return {
            profile.channel_id: profile
            for profile in (
                self.db.query(VideoSourceProfile)
                .filter(VideoSourceProfile.channel_id.in_(normalized))
                .all()
            )
        }

    def list_all(self) -> List[VideoSourceProfile]:
        return (
            self.db.query(VideoSourceProfile)
            .order_by(VideoSourceProfile.status, VideoSourceProfile.channel_name)
            .all()
        )

    def upsert_from_registry(self, configs: Iterable[ChannelConfig]) -> List[VideoSourceProfile]:
        """Ensure every curated channel has a persisted governance profile."""
        unique_configs = dedupe_channel_configs(configs)
        if not unique_configs:
            return []

        existing_profiles = {
            profile.channel_id: profile
            for profile in (
                self.db.query(VideoSourceProfile)
                .filter(
                    VideoSourceProfile.channel_id.in_(
                        [config.channel_id for config in unique_configs]
                    )
                )
                .all()
            )
        }

        profiles: List[VideoSourceProfile] = []
        for config in unique_configs:
            profile = existing_profiles.get(config.channel_id)
            reel_cap = config.effective_daily_reel_cap
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
                    allow_search=config.allow_search,
                    allow_trending=config.allow_trending,
                )
                self.db.add(profile)
                existing_profiles[config.channel_id] = profile
            else:
                profile.channel_name = config.name
                profile.role = config.role.value
                profile.content_format = config.content_format.value
                profile.quality_tier = config.quality_tier.value
                profile.enabled = config.enabled
                profile.curated_seed = True
                profile.daily_video_cap = config.daily_cap
                profile.daily_reel_cap = reel_cap
                profile.allow_search = config.allow_search
                profile.allow_trending = config.allow_trending
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

    def upsert_discovered_channels(
        self, channels: Iterable[Dict[str, str]]
    ) -> List[VideoSourceProfile]:
        """Persist accepted discovery channels so admin/source health can track them."""
        normalized: Dict[str, Dict[str, str]] = {}
        for channel in channels:
            channel_id = (channel.get("channel_id") or "").strip()
            if not channel_id:
                continue
            normalized[channel_id] = channel

        if not normalized:
            return []

        existing_profiles = {
            profile.channel_id: profile
            for profile in (
                self.db.query(VideoSourceProfile)
                .filter(VideoSourceProfile.channel_id.in_(list(normalized.keys())))
                .all()
            )
        }

        profiles: List[VideoSourceProfile] = []
        for channel_id, channel in normalized.items():
            profile = existing_profiles.get(channel_id)
            role = (channel.get("role") or "explainer").strip() or "explainer"
            content_format = (channel.get("content_format") or "mixed").strip() or "mixed"
            quality_tier = (channel.get("quality_tier") or "standard").strip() or "standard"
            channel_name = (channel.get("channel_name") or "YouTube").strip() or "YouTube"

            if profile is None:
                profile = VideoSourceProfile(
                    channel_id=channel_id,
                    channel_name=channel_name,
                    role=role,
                    content_format=content_format,
                    quality_tier=quality_tier,
                    status="discovery",
                    enabled=True,
                    curated_seed=False,
                    daily_video_cap=1,
                    daily_reel_cap=1,
                    allow_curated=False,
                    allow_search=True,
                    allow_trending=True,
                )
                self.db.add(profile)
                existing_profiles[channel_id] = profile
            else:
                profile.channel_name = channel_name
                profile.role = role
                profile.content_format = content_format
                profile.quality_tier = quality_tier
                if not profile.curated_seed and profile.status == "blocked":
                    profile.status = "discovery"

            profiles.append(profile)

        self.db.flush()
        return profiles

    def recent_runs(self, hours_back: int = 168) -> List[VideoDiscoveryRun]:
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        return (
            self.db.query(VideoDiscoveryRun)
            .filter(VideoDiscoveryRun.run_started_at >= cutoff)
            .all()
        )
