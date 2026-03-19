"""
Inventory Health Service.

Provides real-time visibility into content inventory across tiers:
- Tier A (Fresh): published_at within rolling window
- Tier B (Backfill): created_at within backfill window, published_at older
- Tier C (Evergreen): older high-quality items within max age

This service powers:
- /inventory/health endpoint
- Top-up trigger decisions
- Feed blend optimization
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.content import ContentItem, ContentStatus, ContentType
from app.services.video_content_policy import apply_content_policy
from app.video_age_policy import build_surface_age_filters, make_default_policy
from app.video_surface_rules import surface_content_filter, visible_promotion_filter

logger = get_logger(__name__)


class FreshnessTier(str, Enum):
    """Content freshness tier for feed blending."""

    A = "A"  # Fresh: recently published
    B = "B"  # Backfill: recently added, older publication
    C = "C"  # Evergreen: older but high quality


class Surface(str, Enum):
    """Content surfaces for inventory tracking."""

    ARTICLES = "articles"
    VIDEOS = "videos"
    REELS = "reels"


@dataclass
class TierCounts:
    """Counts per freshness tier."""

    tier_a: int = 0
    tier_b: int = 0
    tier_c: int = 0

    @property
    def total(self) -> int:
        return self.tier_a + self.tier_b + self.tier_c

    def to_dict(self) -> Dict[str, int]:
        return {
            "tier_a": self.tier_a,
            "tier_b": self.tier_b,
            "tier_c": self.tier_c,
            "total": self.total,
        }


@dataclass
class SourceDistribution:
    """Per-source content counts."""

    counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, int]:
        # Return top 10 sources
        return dict(sorted(self.counts.items(), key=lambda x: -x[1])[:10])


@dataclass
class SurfaceHealth:
    """Health metrics for a single surface."""

    surface: Surface
    tier_counts: TierCounts
    newest_item_age_seconds: Optional[int]
    oldest_tier_a_age_seconds: Optional[int]
    reservoir_count: int
    min_fresh_threshold: int
    reservoir_threshold: int
    source_distribution: SourceDistribution
    recent_refresh_count: int = 0
    recent_refresh_threshold: int = 0
    refresh_window_hours: int = 0
    is_healthy: bool = True
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surface": self.surface.value,
            "tier_counts": self.tier_counts.to_dict(),
            "newest_item_age_seconds": self.newest_item_age_seconds,
            "oldest_tier_a_age_seconds": self.oldest_tier_a_age_seconds,
            "reservoir_count": self.reservoir_count,
            "min_fresh_threshold": self.min_fresh_threshold,
            "recent_refresh_count": self.recent_refresh_count,
            "recent_refresh_threshold": self.recent_refresh_threshold,
            "refresh_window_hours": self.refresh_window_hours,
            "reservoir_threshold": self.reservoir_threshold,
            "is_healthy": self.is_healthy,
            "issues": self.issues,
            "source_distribution": self.source_distribution.to_dict(),
        }


@dataclass
class InventoryHealth:
    """Overall inventory health across all surfaces."""

    timestamp: datetime
    surfaces: Dict[Surface, SurfaceHealth]
    is_healthy: bool = True
    needs_topup: bool = False
    topup_priority: List[Surface] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "is_healthy": self.is_healthy,
            "needs_topup": self.needs_topup,
            "topup_priority": [s.value for s in self.topup_priority],
            "surfaces": {s.value: h.to_dict() for s, h in self.surfaces.items()},
        }


def _get_surface_config(surface: Surface) -> Dict[str, int]:
    """Get configuration values for a surface."""
    if surface == Surface.ARTICLES:
        return {
            "fresh_hours": settings.ARTICLES_FRESH_PUBLISHED_HOURS,
            "backfill_hours": settings.ARTICLES_BACKFILL_CREATED_HOURS,
            "evergreen_days": settings.ARTICLES_EVERGREEN_MAX_DAYS,
            "min_fresh": settings.MIN_FRESH_ARTICLES,
            "reservoir": settings.RESERVOIR_ARTICLES,
        }
    elif surface == Surface.VIDEOS:
        return {
            "fresh_hours": settings.VIDEOS_FRESH_PUBLISHED_HOURS,
            "backfill_hours": settings.VIDEOS_BACKFILL_CREATED_HOURS,
            "evergreen_days": settings.VIDEOS_EVERGREEN_MAX_DAYS,
            "min_fresh": settings.MIN_FRESH_VIDEOS,
            "refresh_hours": settings.VIDEOS_REFRESH_PUBLISHED_HOURS,
            "min_refresh": settings.MIN_REFRESH_VIDEOS,
            "reservoir": settings.RESERVOIR_VIDEOS,
        }
    else:  # REELS
        return {
            "fresh_hours": settings.REELS_FRESH_PUBLISHED_HOURS,
            "backfill_hours": settings.REELS_BACKFILL_CREATED_HOURS,
            "evergreen_days": settings.REELS_EVERGREEN_MAX_DAYS,
            "min_fresh": settings.MIN_FRESH_REELS,
            "refresh_hours": settings.REELS_REFRESH_PUBLISHED_HOURS,
            "min_refresh": settings.MIN_REFRESH_REELS,
            "reservoir": settings.RESERVOIR_REELS,
        }


def _surface_to_content_type(surface: Surface) -> ContentType:
    """Map surface to content type."""
    return {
        Surface.ARTICLES: ContentType.ARTICLE,
        Surface.VIDEOS: ContentType.VIDEO,
        Surface.REELS: ContentType.REEL,
    }[surface]


def compute_surface_health(
    db: Session, surface: Surface, now: Optional[datetime] = None
) -> SurfaceHealth:
    """
    Compute inventory health for a single surface.

    Args:
        db: Database session
        surface: Which surface to evaluate
        now: Current time (for testing)

    Returns:
        SurfaceHealth with tier counts and diagnostics
    """
    now = now or datetime.utcnow()
    cfg = _get_surface_config(surface)
    content_type = _surface_to_content_type(surface)

    fresh_cutoff = now - timedelta(hours=cfg["fresh_hours"])
    refresh_cutoff = now - timedelta(hours=cfg.get("refresh_hours", cfg["fresh_hours"]))
    backfill_cutoff = now - timedelta(hours=cfg["backfill_hours"])
    evergreen_cutoff = now - timedelta(days=cfg["evergreen_days"])

    # Base query filters – only PROMOTED items count toward inventory health;
    # CANDIDATE stubs are invisible in feeds and must not inflate tier counts.
    base_filter = and_(
        surface_content_filter(surface.value),
        visible_promotion_filter(),
        ContentItem.is_suppressed.is_(False),
        ContentItem.curation_status == ContentStatus.PROMOTED,
    )
    scoped_query = apply_content_policy(db.query(ContentItem), content_type=content_type)

    if surface in (Surface.VIDEOS, Surface.REELS):
        default_policy = make_default_policy(
            fresh_hours=cfg["fresh_hours"],
            backfill_hours=cfg["backfill_hours"],
            evergreen_days=cfg["evergreen_days"],
        )
        age_filters = build_surface_age_filters(now=now, default_policy=default_policy)
        fresh_window_filter = age_filters.fresh
        backfill_window_filter = age_filters.backfill
        evergreen_tier_filter = age_filters.evergreen_tier
        reservoir_filter = age_filters.reservoir
    else:
        fresh_window_filter = ContentItem.published_at >= fresh_cutoff
        backfill_window_filter = and_(
            ContentItem.created_at >= backfill_cutoff,
            ContentItem.published_at < fresh_cutoff,
        )
        evergreen_tier_filter = and_(
            ContentItem.published_at < fresh_cutoff,
            ContentItem.published_at >= evergreen_cutoff,
        )
        reservoir_filter = ContentItem.published_at >= evergreen_cutoff

    # Tier A: published_at within fresh window
    tier_a_count = (
        apply_content_policy(
            db.query(func.count(ContentItem.id)).select_from(ContentItem),
            content_type=content_type,
        )
        .filter(base_filter, fresh_window_filter)
        .scalar()
        or 0
    )

    # Tier B: created_at within backfill window AND published_at older than fresh
    tier_b_count = (
        apply_content_policy(
            db.query(func.count(ContentItem.id)).select_from(ContentItem),
            content_type=content_type,
        )
        .filter(base_filter, backfill_window_filter)
        .scalar()
        or 0
    )

    # Tier C: evergreen (older than fresh, within max age, high quality)
    # We use global_score > 0.3 as quality threshold
    tier_c_count = (
        apply_content_policy(
            db.query(func.count(ContentItem.id)).select_from(ContentItem),
            content_type=content_type,
        )
        .filter(base_filter, evergreen_tier_filter, ContentItem.global_score >= 0.3)
        .scalar()
        or 0
    )

    tier_counts = TierCounts(
        tier_a=tier_a_count,
        tier_b=tier_b_count,
        tier_c=tier_c_count,
    )

    recent_refresh_count = (
        apply_content_policy(
            db.query(func.count(ContentItem.id)).select_from(ContentItem),
            content_type=content_type,
        )
        .filter(base_filter, ContentItem.published_at >= refresh_cutoff)
        .scalar()
        or 0
    )

    # Reservoir count (total available content within max age)
    reservoir_count = (
        apply_content_policy(
            db.query(func.count(ContentItem.id)).select_from(ContentItem),
            content_type=content_type,
        )
        .filter(base_filter, reservoir_filter)
        .scalar()
        or 0
    )

    # Newest item age
    newest = (
        apply_content_policy(
            db.query(func.max(ContentItem.published_at)).select_from(ContentItem),
            content_type=content_type,
        )
        .filter(base_filter, reservoir_filter)
        .scalar()
    )
    newest_age = int((now - newest).total_seconds()) if newest else None

    # Oldest Tier A item age
    oldest_tier_a = (
        apply_content_policy(
            db.query(func.min(ContentItem.published_at)).select_from(ContentItem),
            content_type=content_type,
        )
        .filter(base_filter, fresh_window_filter)
        .scalar()
    )
    oldest_tier_a_age = int((now - oldest_tier_a).total_seconds()) if oldest_tier_a else None

    # Source distribution (for reservoir content)
    source_dist_rows = (
        scoped_query.with_entities(ContentItem.source, func.count(ContentItem.id))
        .filter(base_filter, reservoir_filter)
        .group_by(ContentItem.source)
        .all()
    )

    source_distribution = SourceDistribution(
        counts={row[0] or "Unknown": row[1] for row in source_dist_rows}
    )

    # Evaluate health and issues
    issues = []
    is_healthy = True

    if tier_a_count < cfg["min_fresh"]:
        issues.append(f"Fresh content below minimum: {tier_a_count} < {cfg['min_fresh']}")
        is_healthy = False

    if recent_refresh_count < cfg.get("min_refresh", cfg["min_fresh"]):
        issues.append(
            "Recent refresh below minimum: "
            f"{recent_refresh_count} < {cfg.get('min_refresh', cfg['min_fresh'])} "
            f"in last {cfg.get('refresh_hours', cfg['fresh_hours'])}h"
        )
        is_healthy = False

    if reservoir_count < cfg["reservoir"]:
        issues.append(f"Reservoir below target: {reservoir_count} < {cfg['reservoir']}")
        is_healthy = False

    if newest_age and newest_age > cfg["fresh_hours"] * 3600:
        issues.append(f"No fresh content: newest is {newest_age // 3600}h old")
        is_healthy = False

    return SurfaceHealth(
        surface=surface,
        tier_counts=tier_counts,
        newest_item_age_seconds=newest_age,
        oldest_tier_a_age_seconds=oldest_tier_a_age,
        reservoir_count=reservoir_count,
        min_fresh_threshold=cfg["min_fresh"],
        recent_refresh_count=recent_refresh_count,
        recent_refresh_threshold=cfg.get("min_refresh", cfg["min_fresh"]),
        refresh_window_hours=cfg.get("refresh_hours", cfg["fresh_hours"]),
        reservoir_threshold=cfg["reservoir"],
        source_distribution=source_distribution,
        is_healthy=is_healthy,
        issues=issues,
    )


def compute_inventory_health(db: Session, now: Optional[datetime] = None) -> InventoryHealth:
    """
    Compute inventory health for all surfaces.

    Args:
        db: Database session
        now: Current time (for testing)

    Returns:
        InventoryHealth with per-surface metrics and top-up priorities
    """
    now = now or datetime.utcnow()

    surfaces = {}
    topup_priority = []

    for surface in Surface:
        health = compute_surface_health(db, surface, now)
        surfaces[surface] = health

        if not health.is_healthy:
            topup_priority.append(surface)

    # Sort priority by severity (lowest tier_a percentage first)
    topup_priority.sort(
        key=lambda s: surfaces[s].tier_counts.tier_a / max(surfaces[s].min_fresh_threshold, 1)
    )

    is_healthy = all(h.is_healthy for h in surfaces.values())
    needs_topup = len(topup_priority) > 0

    return InventoryHealth(
        timestamp=now,
        surfaces=surfaces,
        is_healthy=is_healthy,
        needs_topup=needs_topup,
        topup_priority=topup_priority,
    )


# Redis caching for inventory health
_cached_health: Optional[InventoryHealth] = None
_cache_timestamp: Optional[datetime] = None


def get_cached_inventory_health(db: Session, force_refresh: bool = False) -> InventoryHealth:
    """
    Get inventory health with caching.

    Args:
        db: Database session
        force_refresh: Bypass cache and recompute

    Returns:
        InventoryHealth (cached if within TTL)
    """
    global _cached_health, _cache_timestamp

    now = datetime.utcnow()
    ttl = settings.INVENTORY_HEALTH_CACHE_TTL

    if (
        not force_refresh
        and _cached_health is not None
        and _cache_timestamp is not None
        and (now - _cache_timestamp).total_seconds() < ttl
    ):
        return _cached_health

    # Recompute
    health = compute_inventory_health(db, now)
    _cached_health = health
    _cache_timestamp = now

    logger.debug(
        f"Inventory health recomputed: healthy={health.is_healthy}, topup_priority={health.topup_priority}"
    )

    return health


def invalidate_health_cache():
    """Invalidate the inventory health cache (call after ingestion)."""
    global _cached_health, _cache_timestamp
    _cached_health = None
    _cache_timestamp = None


# ── Pipeline counts (CANDIDATE vs PROMOTED) ──────────────────────────────────


def get_pipeline_counts(db: Session) -> Dict[str, Any]:
    """Return per-type CANDIDATE and PROMOTED counts.

    Used by /inventory/health to give visibility into the two-tier pipeline.
    Returns the newest timestamp for each tier so operators can see
    whether the promotion job is running.
    """
    from sqlalchemy import func as sa_func

    window_hours = 48
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)

    rows = (
        apply_content_policy(
            db.query(
                ContentItem.type,
                ContentItem.curation_status,
                sa_func.count(ContentItem.id).label("cnt"),
                sa_func.max(ContentItem.created_at).label("newest"),
            )
        )
        .filter(
            ContentItem.published_at >= cutoff,
            ContentItem.is_suppressed.is_(False),
        )
        .group_by(ContentItem.type, ContentItem.curation_status)
        .all()
    )

    # Structure: {type_name: {status: {count, newest_at}}}
    result: Dict[str, Any] = {}
    for row in rows:
        type_name = row.type.value.lower() + "s"  # "articles", "videos", "reels"
        status_name = row.curation_status.value.lower() if row.curation_status else "promoted"
        if type_name not in result:
            result[type_name] = {}
        result[type_name][status_name] = {
            "count": row.cnt,
            "newest_at": row.newest.isoformat() if row.newest else None,
        }

    return {
        "window_hours": window_hours,
        "per_type": result,
    }
