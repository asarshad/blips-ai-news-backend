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

logger = get_logger(__name__)

DEFAULT_INGESTION_TARGET_OVERRIDES: Dict[str, int] = {}

REEL_AUTO_PAUSE_MIN_ATTEMPTS = 60
REEL_AUTO_PAUSE_MIN_CONVERSION = 0.02
REEL_AUTO_PAUSE_CONSECUTIVE_DAYS = 5


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


def get_reel_auto_pause_decisions(
    *,
    db=None,
    day_utc: date | None = None,
    feed_names: List[str] | None = None,
) -> Dict[str, str]:
    """Return reel feeds that should be auto-paused for the current day."""
    if settings.YOUTUBE_CURATED_ONLY:
        return {}
    if db is None:
        return {}

    names = sorted({name for name in (feed_names or []) if str(name).strip()})
    if not names:
        return {}

    from app.ingestion.time import get_ingestion_day
    from app.models.ingestion_progress import IngestionProgress

    today = day_utc or get_ingestion_day()
    yesterday = today - timedelta(days=1)
    start_day = today - timedelta(days=REEL_AUTO_PAUSE_CONSECUTIVE_DAYS)

    rows = (
        db.query(IngestionProgress)
        .filter(
            IngestionProgress.source_type == "youtube_reel",
            IngestionProgress.feed_name.in_(names),
            IngestionProgress.day_utc >= start_day,
            IngestionProgress.day_utc <= yesterday,
        )
        .all()
    )

    by_feed_day = {(row.feed_name, row.day_utc): row for row in rows}
    paused: Dict[str, str] = {}

    for feed_name in names:
        yesterday_row = by_feed_day.get((feed_name, yesterday))
        attempted_yesterday = int(getattr(yesterday_row, "items_attempted", 0) or 0)
        inserted_yesterday = int(getattr(yesterday_row, "items_ingested", 0) or 0)

        if attempted_yesterday >= REEL_AUTO_PAUSE_MIN_ATTEMPTS and inserted_yesterday == 0:
            paused[feed_name] = (
                f"attempted>={REEL_AUTO_PAUSE_MIN_ATTEMPTS} and inserted=0 on "
                f"{yesterday.isoformat()}"
            )
            continue

        low_conversion_streak = True
        for offset in range(1, REEL_AUTO_PAUSE_CONSECUTIVE_DAYS + 1):
            day_to_check = today - timedelta(days=offset)
            row = by_feed_day.get((feed_name, day_to_check))
            if row is None:
                low_conversion_streak = False
                break

            attempted = int(getattr(row, "items_attempted", 0) or 0)
            inserted = int(getattr(row, "items_ingested", 0) or 0)
            if attempted <= 0:
                low_conversion_streak = False
                break

            conversion = inserted / attempted
            if conversion >= REEL_AUTO_PAUSE_MIN_CONVERSION:
                low_conversion_streak = False
                break

        if low_conversion_streak:
            paused[feed_name] = (
                f"conversion<{REEL_AUTO_PAUSE_MIN_CONVERSION:.0%} for "
                f"{REEL_AUTO_PAUSE_CONSECUTIVE_DAYS} consecutive days"
            )

    return paused


def _fresh_promoted_count(db, content_type: ContentType, *, hours: int) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    query = db.query(ContentItem.id).filter(
        ContentItem.type == content_type,
        ContentItem.curation_status == ContentStatus.PROMOTED,
        ContentItem.is_suppressed.is_(False),
        ContentItem.published_at >= cutoff,
    )
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


def build_defaults(*, db=None, day_utc: date | None = None) -> List[FeedDefault]:
    """Build per-feed daily targets from configured RSS feeds + YouTube channels."""

    from app.integrations.rss_client import RSSClient
    from app.integrations.youtube_channels import ContentFormat
    from app.integrations.youtube_client import YouTubeClient

    overrides = parse_target_overrides()

    defaults: List[FeedDefault] = []
    fill_videos = _should_fill_surface(db, ContentType.VIDEO)
    fill_reels = _should_fill_surface(db, ContentType.REEL)

    # RSS: 1 row per feed
    rss_client = RSSClient()
    for cfg in rss_client.feed_configs:
        key = f"rss:{cfg.name}"
        target = int(overrides.get(key, cfg.daily_cap))
        defaults.append(FeedDefault("rss", cfg.name, max(0, target)))

    # YouTube: split per channel into video vs reel targets based on format
    yt_client = YouTubeClient()
    for cfg in yt_client.channel_configs:
        if cfg.content_format == ContentFormat.LONG_FORM:
            if not fill_videos:
                continue
            key = f"youtube_video:{cfg.name}"
            target = int(overrides.get(key, cfg.daily_cap))
            defaults.append(FeedDefault("youtube_video", cfg.name, max(0, target)))
        elif cfg.content_format == ContentFormat.SHORTS:
            if not fill_reels:
                continue
            key = f"youtube_reel:{cfg.name}"
            target = int(overrides.get(key, cfg.daily_cap))
            defaults.append(FeedDefault("youtube_reel", cfg.name, max(0, target)))
        else:
            if not fill_videos and not fill_reels:
                continue
            # MIXED: split daily_cap across video and reels
            video_target = max(1, int(cfg.daily_cap) // 2)
            reel_target = max(0, int(cfg.daily_cap) - video_target)

            key_v = f"youtube_video:{cfg.name}"
            key_r = f"youtube_reel:{cfg.name}"
            video_target = int(overrides.get(key_v, video_target))
            reel_target = int(overrides.get(key_r, reel_target))

            if fill_videos:
                defaults.append(FeedDefault("youtube_video", cfg.name, max(0, video_target)))
            if fill_reels and reel_target > 0:
                defaults.append(FeedDefault("youtube_reel", cfg.name, max(0, reel_target)))

    reel_feeds = [d.feed_name for d in defaults if d.source_type == "youtube_reel" and d.target > 0]
    paused_reel_feeds = get_reel_auto_pause_decisions(
        db=db,
        day_utc=day_utc,
        feed_names=reel_feeds,
    )

    if paused_reel_feeds:
        for feed_name, reason in paused_reel_feeds.items():
            logger.warning("Auto-paused reel feed for today: %s (%s)", feed_name, reason)
        defaults = [
            d
            for d in defaults
            if not (d.source_type == "youtube_reel" and d.feed_name in paused_reel_feeds)
        ]

    return [d for d in defaults if d.target > 0]
