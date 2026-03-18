"""Checkpointed ingestion defaults.

Isolates deterministic "what should we ingest" config from the runner.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List

from app.core.config import settings
from app.core.logging import get_logger
from app.models.content import ContentItem, ContentStatus, ContentType
from app.services.video_content_policy import apply_content_policy
from app.video_age_policy import build_surface_age_filters, make_default_policy
from app.video_surface_rules import surface_content_filter

logger = get_logger(__name__)

DEFAULT_INGESTION_TARGET_OVERRIDES: Dict[str, int] = {}


@dataclass(frozen=True)
class FeedDefault:
    source_type: str
    feed_name: str
    target: int


def owner_token() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"


def parse_target_overrides() -> Dict[str, int]:
    """Parse INGESTION_TARGET_DEFAULTS.

    Format: JSON object mapping "{source_type}:{feed_name}" -> int.
    """

    result: Dict[str, int] = dict(DEFAULT_INGESTION_TARGET_OVERRIDES)

    raw = os.getenv("INGESTION_TARGET_DEFAULTS", "").strip()
    if not raw:
        return result

    try:
        data = json.loads(raw)
    except Exception:
        logger.warning("Invalid INGESTION_TARGET_DEFAULTS JSON; ignoring")
        return result

    if isinstance(data, dict):
        for k, v in data.items():
            try:
                result[str(k)] = int(v)
            except Exception:
                continue
    return result


def _fresh_promoted_count(db, content_type: ContentType, *, hours: int) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    if content_type == ContentType.VIDEO:
        content_filter = surface_content_filter("videos")
    elif content_type == ContentType.REEL:
        content_filter = surface_content_filter("reels")
    else:
        content_filter = ContentItem.type == content_type

    filters = [
        content_filter,
        ContentItem.curation_status == ContentStatus.PROMOTED,
        ContentItem.is_suppressed.is_(False),
    ]

    if content_type in (ContentType.VIDEO, ContentType.REEL):
        surface_cfg = (
            {
                "fresh_hours": settings.VIDEOS_FRESH_PUBLISHED_HOURS,
                "backfill_hours": settings.VIDEOS_BACKFILL_CREATED_HOURS,
                "evergreen_days": settings.VIDEOS_EVERGREEN_MAX_DAYS,
            }
            if content_type == ContentType.VIDEO
            else {
                "fresh_hours": settings.REELS_FRESH_PUBLISHED_HOURS,
                "backfill_hours": settings.REELS_BACKFILL_CREATED_HOURS,
                "evergreen_days": settings.REELS_EVERGREEN_MAX_DAYS,
            }
        )
        if hours == surface_cfg["fresh_hours"]:
            age_filters = build_surface_age_filters(
                now=datetime.utcnow(),
                default_policy=make_default_policy(
                    fresh_hours=surface_cfg["fresh_hours"],
                    backfill_hours=surface_cfg["backfill_hours"],
                    evergreen_days=surface_cfg["evergreen_days"],
                ),
            )
            filters.append(age_filters.fresh)
        else:
            filters.append(ContentItem.published_at >= cutoff)
    else:
        filters.append(ContentItem.published_at >= cutoff)

    query = db.query(ContentItem.id).filter(*filters)
    query = apply_content_policy(query, content_type=content_type)
    count = query.count()
    return int(count or 0)


def _recent_refresh_requirement(content_type: ContentType) -> tuple[int, int]:
    if content_type == ContentType.VIDEO:
        return settings.VIDEOS_REFRESH_PUBLISHED_HOURS, settings.MIN_REFRESH_VIDEOS
    if content_type == ContentType.REEL:
        return settings.REELS_REFRESH_PUBLISHED_HOURS, settings.MIN_REFRESH_REELS
    return settings.ARTICLES_FRESH_PUBLISHED_HOURS, settings.MIN_FRESH_ARTICLES


def _should_fill_surface(db, content_type: ContentType) -> bool:
    if db is None:
        return True

    if content_type == ContentType.VIDEO:
        fresh_count = _fresh_promoted_count(
            db,
            ContentType.VIDEO,
            hours=settings.VIDEOS_FRESH_PUBLISHED_HOURS,
        )
        refresh_hours, refresh_min = _recent_refresh_requirement(ContentType.VIDEO)
        recent_count = _fresh_promoted_count(
            db,
            ContentType.VIDEO,
            hours=refresh_hours,
        )
        return fresh_count < settings.MIN_FRESH_VIDEOS or recent_count < refresh_min

    fresh_count = _fresh_promoted_count(
        db,
        ContentType.REEL,
        hours=settings.REELS_FRESH_PUBLISHED_HOURS,
    )
    refresh_hours, refresh_min = _recent_refresh_requirement(ContentType.REEL)
    recent_count = _fresh_promoted_count(
        db,
        ContentType.REEL,
        hours=refresh_hours,
    )
    return fresh_count < settings.MIN_FRESH_REELS or recent_count < refresh_min


def _mixed_targets(cfg) -> tuple[int, int]:
    """Resolve legacy mixed-channel targets into separate video/reel rows."""
    daily_reel_cap = getattr(cfg, "daily_reel_cap", None)
    reels_enabled = getattr(cfg, "reels_enabled", False)

    if daily_reel_cap is not None:
        video_target = max(0, int(cfg.daily_cap))
        reel_target = max(0, int(getattr(cfg, "effective_daily_reel_cap", 0)))
        return video_target, reel_target

    if not reels_enabled:
        return max(0, int(cfg.daily_cap)), 0

    video_target = max(1, int(cfg.daily_cap) // 2)
    reel_target = max(0, int(cfg.daily_cap) - video_target)
    return video_target, reel_target


def _append_youtube_defaults(
    defaults: List[FeedDefault],
    *,
    configs,
    overrides: Dict[str, int],
    include_video: bool,
    include_reel: bool,
) -> None:
    """Append YouTube defaults for the selected surfaces."""
    from app.integrations.youtube_channels import ContentFormat

    for cfg in configs:
        if cfg.content_format == ContentFormat.LONG_FORM:
            if include_video:
                key = f"youtube_video:{cfg.name}"
                target = int(overrides.get(key, cfg.daily_cap))
                defaults.append(FeedDefault("youtube_video", cfg.name, max(0, target)))

            if include_reel and getattr(cfg, "reels_enabled", False):
                reel_cap = max(0, int(getattr(cfg, "effective_daily_reel_cap", 0)))
                if reel_cap > 0:
                    key = f"youtube_reel:{cfg.name}"
                    target = int(overrides.get(key, reel_cap))
                    defaults.append(FeedDefault("youtube_reel", cfg.name, max(0, target)))
            continue

        if cfg.content_format == ContentFormat.SHORTS:
            if not include_reel:
                continue
            key = f"youtube_reel:{cfg.name}"
            target = int(overrides.get(key, cfg.daily_cap))
            defaults.append(FeedDefault("youtube_reel", cfg.name, max(0, target)))
            continue

        video_target, reel_target = _mixed_targets(cfg)
        key_v = f"youtube_video:{cfg.name}"
        key_r = f"youtube_reel:{cfg.name}"
        video_target = int(overrides.get(key_v, video_target))
        reel_target = int(overrides.get(key_r, reel_target))

        if include_video and video_target > 0:
            defaults.append(FeedDefault("youtube_video", cfg.name, max(0, video_target)))
        if include_reel and reel_target > 0:
            defaults.append(FeedDefault("youtube_reel", cfg.name, max(0, reel_target)))


def build_defaults(*, db=None, day_utc: date | None = None) -> List[FeedDefault]:
    """Build per-feed daily targets from configured RSS feeds + YouTube channels."""

    from app.integrations.rss_client import RSSClient
    from app.integrations.youtube_channels import IngestionStream
    from app.integrations.youtube_client import YouTubeClient

    overrides = parse_target_overrides()

    defaults: List[FeedDefault] = []

    # RSS: 1 row per feed
    rss_client = RSSClient()
    for cfg in rss_client.feed_configs:
        key = f"rss:{cfg.name}"
        target = int(overrides.get(key, cfg.daily_cap))
        defaults.append(FeedDefault("rss", cfg.name, max(0, target)))

    # YouTube: primary stream is always created for continuous ingestion.
    # Expansion stream is only activated when a surface needs fill.
    yt_client = YouTubeClient()
    primary_configs = [
        cfg
        for cfg in yt_client.channel_configs
        if getattr(cfg, "ingestion_stream", IngestionStream.PRIMARY) != IngestionStream.EXPANSION
    ]
    expansion_configs = [
        cfg
        for cfg in yt_client.channel_configs
        if getattr(cfg, "ingestion_stream", IngestionStream.PRIMARY) == IngestionStream.EXPANSION
    ]

    _append_youtube_defaults(
        defaults,
        configs=primary_configs,
        overrides=overrides,
        include_video=True,
        include_reel=True,
    )

    if expansion_configs:
        need_video_fill = _should_fill_surface(db, ContentType.VIDEO)
        need_reel_fill = _should_fill_surface(db, ContentType.REEL)
        _append_youtube_defaults(
            defaults,
            configs=expansion_configs,
            overrides=overrides,
            include_video=need_video_fill,
            include_reel=need_reel_fill,
        )

    return [d for d in defaults if d.target > 0]
