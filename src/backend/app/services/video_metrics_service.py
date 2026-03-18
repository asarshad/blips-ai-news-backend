"""Admin-visible supply, lane, and source metrics for videos and reels."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.content import ContentItem, ContentStatus, ContentType, EventType, InteractionEvent
from app.models.video_source import VideoDiscoveryRun, VideoSourceProfile
from app.services.inventory_service import Surface, SurfaceHealth, compute_surface_health
from app.services.video_baseline_service import load_baseline_snapshot, numeric_delta
from app.services.video_content_policy import apply_content_policy
from app.services.video_discovery_provenance import query_label_from_discovered_via
from app.services.video_source_service import refresh_video_source_health


def _surface_type(surface: str) -> ContentType:
    return ContentType.REEL if surface == "reels" else ContentType.VIDEO


def _inventory_surface(surface: str) -> Surface:
    return Surface.REELS if surface == "reels" else Surface.VIDEOS


def _surface_floor(surface: str) -> int:
    return settings.MIN_FRESH_REELS if surface == "reels" else settings.MIN_FRESH_VIDEOS


def _surface_window_hours(surface: str) -> int:
    return (
        settings.REELS_FRESH_PUBLISHED_HOURS
        if surface == "reels"
        else settings.VIDEOS_FRESH_PUBLISHED_HOURS
    )


def _window_label(hours: int) -> str:
    if hours == 24:
        return "24h"
    if hours % 24 == 0:
        return f"{hours // 24}d"
    return f"{hours}h"


def _comparison_window(
    *,
    now: datetime,
    window_hours: int,
    lag: timedelta,
) -> tuple[datetime, datetime]:
    end = now - lag
    start = end - timedelta(hours=window_hours)
    return start, end


def _channel_key(item: ContentItem) -> str:
    return item.channel_id or item.source or "unknown"


def _promotion_rate(promoted_count: int, candidate_count: int) -> float | None:
    if candidate_count <= 0:
        return None
    return round(promoted_count / candidate_count * 100, 2)


def _profile_lookup(db: Session) -> Dict[str, VideoSourceProfile]:
    return {profile.channel_id: profile for profile in db.query(VideoSourceProfile).all()}


def _primary_topic(item: ContentItem) -> str:
    topics = item.topics or []
    return topics[0] if topics else "Technology"


def _role_for_item(item: ContentItem, profiles: Dict[str, VideoSourceProfile]) -> str:
    profile = profiles.get(item.channel_id or "")
    return profile.role if profile else "unknown"


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return round(ordered[idx], 2)


def _load_promoted_items(
    db: Session,
    surface: str,
    *,
    start: datetime,
    end: Optional[datetime] = None,
) -> List[ContentItem]:
    query = (
        apply_content_policy(
            db.query(ContentItem),
            content_type=_surface_type(surface),
        )
        .filter(
            ContentItem.type == _surface_type(surface),
            ContentItem.curation_status == ContentStatus.PROMOTED,
            ContentItem.is_suppressed.is_(False),
            ContentItem.published_at >= start,
        )
        .order_by(
            ContentItem.published_at.desc(),
            ContentItem.promotion_score.desc().nullslast(),
            ContentItem.global_score.desc(),
        )
    )
    if end is not None:
        query = query.filter(ContentItem.published_at < end)
    return query.all()


def _surface_metrics(
    db: Session,
    surface: str,
    *,
    start: datetime,
    end: Optional[datetime] = None,
    profiles: Optional[Dict[str, VideoSourceProfile]] = None,
) -> Dict[str, Any]:
    profiles = profiles or _profile_lookup(db)
    items = _load_promoted_items(db, surface, start=start, end=end)
    now = datetime.utcnow()
    top20 = items[:20]
    top50 = items[:50]

    ages = [
        round((now - item.published_at).total_seconds() / 3600, 2)
        for item in top20
        if item.published_at
    ]
    channel_counts = Counter(_channel_key(item) for item in top20)
    dominant_channel, dominant_count = (
        channel_counts.most_common(1)[0] if channel_counts else ("", 0)
    )
    role_counts = Counter(_role_for_item(item, profiles) for item in top50)
    topic_counts = Counter(_primary_topic(item) for item in top50)
    official_share = sum(1 for item in top20 if _role_for_item(item, profiles) == "official")

    interactions = {
        event_type: count
        for event_type, count in (
            apply_content_policy(
                db.query(InteractionEvent.event_type, func.count(InteractionEvent.id)).join(
                    ContentItem, ContentItem.id == InteractionEvent.content_item_id
                ),
                content_type=_surface_type(surface),
            )
            .filter(
                ContentItem.type == _surface_type(surface),
                InteractionEvent.created_at >= start,
                *([InteractionEvent.created_at < end] if end is not None else []),
            )
            .group_by(InteractionEvent.event_type)
            .all()
        )
    }

    runs = (
        db.query(VideoDiscoveryRun)
        .filter(
            VideoDiscoveryRun.surface == surface,
            VideoDiscoveryRun.run_started_at >= start,
            *([VideoDiscoveryRun.run_started_at < end] if end is not None else []),
        )
        .all()
    )
    candidate_count = sum(run.candidate_count for run in runs)
    duplicate_rejections = sum(run.duplicate_rejections for run in runs)
    clickbait_rejections = sum(run.clickbait_rejections for run in runs)
    window_end = end or now
    window_hours = round((window_end - start).total_seconds() / 3600, 2)
    floor = _surface_floor(surface)

    return {
        "inventory_window_hours": window_hours,
        "inventory_window_label": _window_label(int(window_hours)),
        "floor_target": floor,
        "fresh_inventory_window": len(items),
        "fresh_inventory_24h": len(items),
        "median_age_top20_hours": round(median(ages), 2) if ages else None,
        "p95_age_top20_hours": _percentile(ages, 95.0),
        "distinct_active_channels_window": len({_channel_key(item) for item in items}),
        "distinct_active_channels_24h": len({_channel_key(item) for item in items}),
        "dominant_channel": dominant_channel or None,
        "dominant_channel_pct_top20": round(dominant_count / max(len(top20), 1) * 100, 2),
        "role_coverage_50": sorted(role for role, count in role_counts.items() if count > 0),
        "category_coverage_50": sorted(topic for topic, count in topic_counts.items() if count > 0),
        "official_share_top20": round(official_share / max(len(top20), 1) * 100, 2),
        "empty_feed_rate": round(
            interactions.get(EventType.CAUGHT_UP, 0)
            / max(interactions.get(EventType.VIDEO_IMPRESSION, 0), 1)
            * 100,
            2,
        ),
        "caught_up_rate": round(
            interactions.get(EventType.CAUGHT_UP, 0)
            / max(interactions.get(EventType.VIDEO_IMPRESSION, 0), 1)
            * 100,
            2,
        ),
        "clickbait_rejection_rate": round(clickbait_rejections / max(candidate_count, 1) * 100, 2),
        "duplicate_rejection_rate": round(duplicate_rejections / max(candidate_count, 1) * 100, 2),
        "inventory_state": _inventory_state(surface, len(items), len(top20), dominant_count),
        "top_items": [
            {
                "id": item.id,
                "title": item.title,
                "channel_id": item.channel_id,
                "source": item.source,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "promotion_score": item.promotion_score,
            }
            for item in top20
        ],
    }


def _inventory_state(
    surface: str,
    fresh_inventory: int,
    top_count: int,
    dominant_count: int,
    *,
    recent_refresh_count: Optional[int] = None,
    recent_refresh_threshold: Optional[int] = None,
    is_surface_healthy: Optional[bool] = None,
) -> str:
    floor = _surface_floor(surface)
    dominant_pct = dominant_count / max(top_count, 1)
    if fresh_inventory < floor:
        return "warming_up"
    if (
        recent_refresh_count is not None
        and recent_refresh_threshold is not None
        and recent_refresh_count < recent_refresh_threshold
    ):
        return "needs_refresh"
    if surface == "reels" and dominant_pct > 0.15:
        return "imbalanced"
    if surface == "videos" and dominant_pct > 0.20:
        return "imbalanced"
    if is_surface_healthy is False:
        return "degraded"
    return "healthy"


def _merge_surface_health(
    surface: str,
    metrics: Dict[str, Any],
    surface_health: SurfaceHealth,
) -> Dict[str, Any]:
    top_count = min(metrics.get("fresh_inventory_window", 0), 20)
    dominant_pct = float(metrics.get("dominant_channel_pct_top20") or 0.0)
    dominant_count = round(dominant_pct * top_count / 100) if top_count else 0

    return {
        **metrics,
        "recent_refresh_count": surface_health.recent_refresh_count,
        "recent_refresh_threshold": surface_health.recent_refresh_threshold,
        "refresh_window_hours": surface_health.refresh_window_hours,
        "reservoir_count": surface_health.reservoir_count,
        "reservoir_threshold": surface_health.reservoir_threshold,
        "newest_item_age_seconds": surface_health.newest_item_age_seconds,
        "oldest_tier_a_age_seconds": surface_health.oldest_tier_a_age_seconds,
        "is_healthy": surface_health.is_healthy,
        "issues": list(surface_health.issues),
        "inventory_state": _inventory_state(
            surface,
            metrics.get("fresh_inventory_window", 0),
            top_count,
            dominant_count,
            recent_refresh_count=surface_health.recent_refresh_count,
            recent_refresh_threshold=surface_health.recent_refresh_threshold,
            is_surface_healthy=surface_health.is_healthy,
        ),
    }


def _with_deltas(
    current: Dict[str, Any],
    baseline: Optional[Dict[str, Any]],
    previous: Dict[str, Any],
    weekly: Dict[str, Any],
) -> Dict[str, Any]:
    numeric_keys = [
        "fresh_inventory_24h",
        "median_age_top20_hours",
        "p95_age_top20_hours",
        "distinct_active_channels_24h",
        "dominant_channel_pct_top20",
        "official_share_top20",
        "empty_feed_rate",
        "caught_up_rate",
        "clickbait_rejection_rate",
        "duplicate_rejection_rate",
    ]
    deltas: Dict[str, Dict[str, Optional[float]]] = {}
    for key in numeric_keys:
        baseline_value = baseline.get(key) if baseline else None
        deltas[key] = {
            "baseline": numeric_delta(current.get(key), baseline_value),
            "vs_24h": numeric_delta(current.get(key), previous.get(key)),
            "vs_7d": numeric_delta(current.get(key), weekly.get(key)),
        }
    return deltas


def compute_video_supply_metrics(
    db: Session,
    *,
    baseline_tag: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute supply/freshness/coverage KPIs for videos and reels."""
    refresh_video_source_health(db)
    profiles = _profile_lookup(db)
    now = datetime.utcnow()
    baseline = load_baseline_snapshot(baseline_tag)
    surface_health = {
        surface: compute_surface_health(db, _inventory_surface(surface), now)
        for surface in ("videos", "reels")
    }

    surfaces: Dict[str, Any] = {}
    for surface in ("videos", "reels"):
        window_hours = _surface_window_hours(surface)
        current_start, current_end = _comparison_window(
            now=now,
            window_hours=window_hours,
            lag=timedelta(),
        )
        prev_24h_start, prev_24h_end = _comparison_window(
            now=now,
            window_hours=window_hours,
            lag=timedelta(hours=24),
        )
        prev_7d_start, prev_7d_end = _comparison_window(
            now=now,
            window_hours=window_hours,
            lag=timedelta(days=7),
        )
        current = _surface_metrics(
            db,
            surface,
            start=current_start,
            end=current_end,
            profiles=profiles,
        )
        current = _merge_surface_health(surface, current, surface_health[surface])
        previous = _surface_metrics(
            db,
            surface,
            start=prev_24h_start,
            end=prev_24h_end,
            profiles=profiles,
        )
        weekly = _surface_metrics(
            db,
            surface,
            start=prev_7d_start,
            end=prev_7d_end,
            profiles=profiles,
        )
        baseline_surface = (baseline or {}).get("video_supply", {}).get(surface)
        surfaces[surface] = {
            **current,
            "deltas": _with_deltas(current, baseline_surface, previous, weekly),
        }

    return {
        "as_of": now.isoformat(),
        "baseline_tag": (baseline or {}).get("tag") if baseline else None,
        "surfaces": surfaces,
    }


def compute_video_lane_metrics(
    db: Session,
    hours: int = 24,
    *,
    breakdown: str | None = None,
) -> Dict[str, Any]:
    """Aggregate discovery lane candidate and promoted performance."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    breakdown = (breakdown or "").strip().lower() or None
    use_query_breakdown = breakdown == "query"

    run_rows = db.query(VideoDiscoveryRun).filter(VideoDiscoveryRun.run_started_at >= cutoff).all()
    lane_metrics: Dict[tuple[str, str], Dict[str, Any]] = defaultdict(
        lambda: {
            "candidates": 0,
            "duplicate_rejections": 0,
            "clickbait_rejections": 0,
            "filtered_non_english": 0,
            "filtered_live": 0,
            "filtered_off_topic": 0,
            "filtered_format": 0,
        }
    )

    for run in run_rows:
        label = run.query_label or run.lane or "unknown"
        key = (run.surface, label if use_query_breakdown else (run.lane or "unknown"))
        lane_metrics[key]["candidates"] += run.candidate_count
        lane_metrics[key]["duplicate_rejections"] += run.duplicate_rejections
        lane_metrics[key]["clickbait_rejections"] += run.clickbait_rejections
        lane_metrics[key]["filtered_non_english"] += run.filtered_non_english
        lane_metrics[key]["filtered_live"] += run.filtered_live
        lane_metrics[key]["filtered_off_topic"] += run.filtered_off_topic
        lane_metrics[key]["filtered_format"] += run.filtered_format

    promoted_rows = (
        db.query(ContentItem)
        .filter(
            ContentItem.created_at >= cutoff,
            ContentItem.type.in_([ContentType.VIDEO, ContentType.REEL]),
            ContentItem.acquisition_lane.isnot(None),
            ContentItem.curation_status == ContentStatus.PROMOTED,
        )
        .all()
    )
    now = datetime.utcnow()
    promoted_by_lane: Dict[tuple[str, str], List[ContentItem]] = defaultdict(list)
    for item in promoted_rows:
        surface = "reels" if item.type == ContentType.REEL else "videos"
        label = (
            query_label_from_discovered_via(
                item.discovered_via,
                acquisition_lane=item.acquisition_lane,
            )
            if use_query_breakdown
            else (item.acquisition_lane or "unknown")
        )
        if label is None:
            continue
        promoted_by_lane[(surface, label)].append(item)

    lanes: Dict[str, List[Dict[str, Any]]] = {"videos": [], "reels": []}
    label_key = "query_label" if use_query_breakdown else "lane"
    for surface in ("videos", "reels"):
        labels = {lane for lane_surface, lane in lane_metrics if lane_surface == surface}
        labels.update(lane for lane_surface, lane in promoted_by_lane if lane_surface == surface)
        for label in sorted(labels):
            current = lane_metrics[(surface, label)]
            promoted = promoted_by_lane[(surface, label)]
            ages = [
                round((now - item.published_at).total_seconds() / 3600, 2)
                for item in promoted
                if item.published_at
            ]
            candidate_count = current["candidates"]
            promotion_rate = _promotion_rate(len(promoted), candidate_count)
            row = {
                label_key: label,
                "candidates": candidate_count,
                "promoted": len(promoted),
                "promotion_rate": promotion_rate,
                "median_promoted_age": round(median(ages), 2) if ages else None,
                "distinct_promoted_channels": len({_channel_key(item) for item in promoted}),
                "duplicate_rejection_rate": round(
                    current["duplicate_rejections"] / max(candidate_count, 1) * 100,
                    2,
                ),
                "clickbait_rejection_rate": round(
                    current["clickbait_rejections"] / max(candidate_count, 1) * 100,
                    2,
                ),
            }
            if use_query_breakdown:
                row.update(
                    {
                        "filtered_non_english": current["filtered_non_english"],
                        "filtered_live": current["filtered_live"],
                        "filtered_off_topic": current["filtered_off_topic"],
                        "filtered_format": current["filtered_format"],
                    }
                )
            lanes[surface].append(row)

    payload = {
        "as_of": now.isoformat(),
        "window_hours": hours,
        "surfaces": lanes,
    }
    if use_query_breakdown:
        payload["breakdown"] = "query"
    return payload


def compute_video_source_metrics(db: Session) -> Dict[str, Any]:
    """Return channel health rows for the admin portal."""
    profiles = refresh_video_source_health(db)
    return {
        "as_of": datetime.utcnow().isoformat(),
        "sources": [
            {
                "channel_id": profile.channel_id,
                "channel_name": profile.channel_name,
                "role": profile.role,
                "content_format": profile.content_format,
                "quality_tier": profile.quality_tier,
                "status": profile.status,
                "enabled": profile.enabled,
                "score_7d": profile.score_7d,
                "promoted_share": round(profile.promoted_share_7d * 100, 2),
                "suppression_rate": round(profile.suppression_rate_7d * 100, 2),
                "early_skip_rate": round(profile.early_skip_rate_7d * 100, 2),
                "completion_rate": round(profile.completion_rate_7d * 100, 2),
                "save_share_rate": round(profile.save_share_rate_7d * 100, 2),
                "last_seen_at": profile.last_seen_at.isoformat() if profile.last_seen_at else None,
                "last_promoted_at": profile.last_promoted_at.isoformat()
                if profile.last_promoted_at
                else None,
            }
            for profile in profiles
        ],
    }
