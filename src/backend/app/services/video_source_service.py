"""Video source governance bootstrap and health scoring."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Iterable, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.youtube_channels import (
    CHANNEL_REGISTRY,
    ChannelConfig,
    dedupe_channel_configs,
    get_channel_by_id,
    get_channel_by_name,
)
from app.models.content import ContentItem, ContentStatus, ContentType, EventType, InteractionEvent
from app.models.video_source import VideoSourceProfile
from app.repositories.video_source_repo import VideoSourceProfileRepository
from app.services.promotion_service import compute_clickbait_penalty


def bootstrap_video_source_profiles(
    db: Session, configs: Iterable[ChannelConfig] | None = None
) -> List[VideoSourceProfile]:
    """Persist the curated registry into profile rows when missing."""
    repo = VideoSourceProfileRepository(db)
    profiles = repo.upsert_from_registry(dedupe_channel_configs(configs or CHANNEL_REGISTRY))
    db.commit()
    return profiles


def _metadata_lookback_days() -> int:
    return max(settings.VIDEOS_EVERGREEN_MAX_DAYS, settings.REELS_EVERGREEN_MAX_DAYS)


def _looks_like_youtube_url(value: str | None) -> bool:
    lowered = (value or "").lower()
    return "youtube.com" in lowered or "youtu.be" in lowered


def _infer_lane(item: ContentItem) -> str | None:
    if item.acquisition_lane:
        return item.acquisition_lane

    discovered_via = (item.discovered_via or "").strip().lower()
    if discovered_via.startswith("yt_"):
        lane = discovered_via.removeprefix("yt_")
        if lane in {"curated", "search", "trending"}:
            return lane
    return None


def repair_video_source_metadata(
    db: Session,
    *,
    lookback_days: int | None = None,
) -> Dict[str, int]:
    """Backfill missing curated video/reel source metadata for recent YouTube rows."""
    cutoff = datetime.utcnow() - timedelta(days=lookback_days or _metadata_lookback_days())

    items = (
        db.query(ContentItem)
        .filter(
            ContentItem.type.in_([ContentType.VIDEO, ContentType.REEL]),
            ContentItem.published_at >= cutoff,
            (
                ContentItem.channel_id.is_(None)
                | ContentItem.acquisition_lane.is_(None)
                | ContentItem.source_status.is_(None)
            ),
        )
        .order_by(ContentItem.published_at.desc())
        .all()
    )

    scanned = 0
    matched = 0
    updated = 0
    unresolved = 0

    for item in items:
        scanned += 1
        if not any(
            _looks_like_youtube_url(candidate)
            for candidate in (item.source_url, item.video_url, item.canonical_url)
        ):
            continue

        channel_config = get_channel_by_id(item.channel_id or "")
        if channel_config is None:
            channel_config = get_channel_by_name(item.source or "")

        lane = _infer_lane(item)
        if lane is None and channel_config is not None:
            lane = "curated"

        if channel_config is None and lane is None:
            unresolved += 1
            continue

        matched += 1
        changed = False

        if item.channel_id is None and channel_config is not None:
            item.channel_id = channel_config.channel_id
            changed = True

        if item.acquisition_lane is None and lane is not None:
            item.acquisition_lane = lane
            changed = True

        if item.source_status is None and channel_config is not None:
            item.source_status = "core" if channel_config.enabled else "blocked"
            changed = True

        if changed:
            updated += 1

    if updated:
        db.commit()

    return {
        "scanned": scanned,
        "matched": matched,
        "updated": updated,
        "unresolved": unresolved,
        "lookback_days": lookback_days or _metadata_lookback_days(),
    }


def _graduation_cooldown_days() -> int:
    """Minimum days between status transitions to prevent oscillation."""
    return 7


def _probation_days() -> int:
    """Post-graduation probation window for discovery→rotation channels."""
    return 14


def _demotion_promotion_floor() -> float:
    """Promotion rate below which demotion is considered."""
    return 0.10


def refresh_video_source_health(db: Session, hours_back: int = 24 * 7) -> List[VideoSourceProfile]:
    """Update rolling 7-day health stats and status for video source profiles."""
    repair_video_source_metadata(db)
    profiles = bootstrap_video_source_profiles(db)
    cutoff = datetime.utcnow() - timedelta(hours=hours_back)

    promoted_total = (
        db.query(func.count(ContentItem.id))
        .filter(
            ContentItem.channel_id.isnot(None),
            ContentItem.created_at >= cutoff,
            ContentItem.curation_status == ContentStatus.PROMOTED,
        )
        .scalar()
        or 0
    )

    for profile in profiles:
        items = (
            db.query(ContentItem)
            .filter(
                ContentItem.channel_id == profile.channel_id,
                ContentItem.created_at >= cutoff,
            )
            .all()
        )
        total = len(items)
        promoted_items = [item for item in items if item.curation_status == ContentStatus.PROMOTED]
        promoted_count = len(promoted_items)
        suppressed_count = sum(1 for item in items if item.is_suppressed)
        clickbait_count = sum(
            1 for item in items if compute_clickbait_penalty(item.title or "") >= 0.3
        )
        fresh_yield_count = sum(
            1
            for item in promoted_items
            if item.published_at and item.published_at >= datetime.utcnow() - timedelta(hours=24)
        )

        event_counts: Dict[EventType, int] = {
            event_type: count
            for event_type, count in (
                db.query(InteractionEvent.event_type, func.count(InteractionEvent.id))
                .join(ContentItem, ContentItem.id == InteractionEvent.content_item_id)
                .filter(
                    ContentItem.channel_id == profile.channel_id,
                    InteractionEvent.created_at >= cutoff,
                )
                .group_by(InteractionEvent.event_type)
                .all()
            )
        }

        impressions = event_counts.get(EventType.VIDEO_IMPRESSION, 0)
        starts = event_counts.get(EventType.VIDEO_START, 0)
        completions = event_counts.get(EventType.VIDEO_95PCT, 0)
        skips = event_counts.get(EventType.VIDEO_SKIP_LT_2S, 0)
        saves = event_counts.get(EventType.VIDEO_SAVE, 0) + event_counts.get(EventType.SAVE, 0)
        shares = event_counts.get(EventType.VIDEO_SHARE, 0) + event_counts.get(EventType.SHARE, 0)

        profile.promotion_rate_7d = round(promoted_count / max(total, 1), 4)
        profile.suppression_rate_7d = round(suppressed_count / max(total, 1), 4)
        profile.clickbait_rate_7d = round(clickbait_count / max(total, 1), 4)
        profile.freshness_yield_7d = round(fresh_yield_count / max(promoted_count, 1), 4)
        profile.promoted_share_7d = round(promoted_count / max(promoted_total, 1), 4)
        profile.early_skip_rate_7d = round(skips / max(starts, 1), 4)
        profile.completion_rate_7d = round(completions / max(starts, 1), 4)
        profile.save_share_rate_7d = round((saves + shares) / max(starts, 1), 4)
        profile.post_start_consumption_7d = round(
            min(
                (
                    (starts / max(impressions, 1)) * 0.35
                    + profile.completion_rate_7d * 0.45
                    + profile.save_share_rate_7d * 0.20
                ),
                1.0,
            ),
            4,
        )
        profile.score_7d = round(
            min(
                max(
                    profile.promotion_rate_7d * 0.35
                    + (1.0 - profile.suppression_rate_7d) * 0.15
                    + (1.0 - profile.clickbait_rate_7d) * 0.10
                    + profile.freshness_yield_7d * 0.20
                    + profile.post_start_consumption_7d * 0.20,
                    0.0,
                ),
                1.0,
            ),
            4,
        )

        last_seen = max((item.published_at for item in items if item.published_at), default=None)
        last_promoted = max(
            (item.published_at for item in promoted_items if item.published_at),
            default=None,
        )
        profile.last_seen_at = last_seen
        profile.last_promoted_at = last_promoted

        old_status = profile.status
        new_status = _compute_graduated_status(
            profile=profile,
            promoted_count_7d=promoted_count,
            db=db,
            cutoff_30d=datetime.utcnow() - timedelta(days=30),
        )
        if new_status != old_status:
            profile.status = new_status
            profile.status_changed_at = datetime.utcnow()
            if old_status == "discovery" and new_status == "rotation":
                profile.probation_until = datetime.utcnow() + timedelta(days=_probation_days())

    db.commit()
    return profiles


def _compute_graduated_status(
    *,
    profile: VideoSourceProfile,
    promoted_count_7d: int,
    db: Session,
    cutoff_30d: datetime,
) -> str:
    """Determine channel status using graduation rules with anti-oscillation."""
    if not profile.enabled:
        return "blocked"

    now = datetime.utcnow()
    cooldown = timedelta(days=_graduation_cooldown_days())
    changed_at = profile.status_changed_at
    in_cooldown = changed_at is not None and (now - changed_at) < cooldown

    # Hard blocks override everything
    if profile.score_7d < 0.25 or profile.suppression_rate_7d >= 0.6:
        return "blocked"

    current = profile.status or "discovery"

    # Promotion: discovery → rotation
    if current == "discovery" and not in_cooldown:
        if (
            promoted_count_7d >= 3
            and profile.promotion_rate_7d >= 0.25
            and profile.clickbait_rate_7d < 0.10
        ):
            return "rotation"

    # Promotion: rotation → core
    if current == "rotation" and not in_cooldown:
        promoted_30d = (
            db.query(func.count(ContentItem.id))
            .filter(
                ContentItem.channel_id == profile.channel_id,
                ContentItem.created_at >= cutoff_30d,
                ContentItem.curation_status == ContentStatus.PROMOTED,
            )
            .scalar()
            or 0
        )
        if (
            promoted_30d >= 10
            and profile.promotion_rate_7d >= 0.40
            and profile.freshness_yield_7d >= 0.30
        ):
            return "core"

    # Demotion: core/rotation → discovery when promotion_rate_7d too low
    if current in ("core", "rotation") and not in_cooldown:
        if profile.promotion_rate_7d < _demotion_promotion_floor():
            # Channels on probation use shorter demotion window (checked by
            # the caller having already set probation_until)
            probation = profile.probation_until
            on_probation = probation is not None and now < probation
            # On probation: demote immediately when below floor
            # Off probation: still demote (the 7d rate already captures a
            # sustained low period since each refresh uses rolling 7d data)
            if on_probation or profile.promotion_rate_7d < _demotion_promotion_floor():
                return "discovery"

    return current
