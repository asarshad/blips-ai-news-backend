"""
Metrics API endpoints for operational monitoring.

These endpoints provide detailed insights into system health and performance.
Includes extraction pipeline metrics and per-source health scoring.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.auth import require_admin_key
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.extraction.metrics import extraction_metrics
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.models.ingestion_progress import IngestionProgress
from app.models.source import SourceDailyStat
from app.services.ai_metrics import compute_ai_feed_metrics
from app.services.ai_usage_metrics_service import compute_ai_usage_metrics
from app.services.article_supply_metrics_service import compute_article_supply_metrics
from app.services.feed_health import compute_inventory_health
from app.services.freshness_metrics_service import compute_freshness_metrics
from app.services.strategic_content_health_service import compute_strategic_content_health
from app.services.video_metrics_service import (
    compute_video_lane_metrics,
    compute_video_source_metrics,
    compute_video_supply_metrics,
)
from app.services.worker_lane_metrics import read_lane_heartbeats

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/metrics", tags=["metrics"])


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _curation_mix_bucket(discovered_via: Any) -> str:
    """Map discovery lanes into curated vs discovery mix buckets."""
    lane = str(discovered_via or "unknown").lower()
    return (
        "discovery"
        if any(token in lane for token in ("discovery", "search", "trending"))
        else "curated"
    )


@router.get("/sources", dependencies=[Depends(require_admin_key)])
def get_source_health_metrics(db: Session = Depends(get_db)):
    """
    Get per-source ingestion health metrics.

    Returns:
        - Per-feed status: last success, items ingested today, retry counts
        - Per-source aggregates: total inserted today, average success rate
        - Problem feeds: any with status=failed or high retry counts

    Requires ADMIN_API_KEY.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)

    try:
        from app.models.video_source import VideoSourceProfile

        # Get today's ingestion progress for all feeds
        progress_rows = (
            db.query(IngestionProgress)
            .filter(IngestionProgress.day_utc.in_([today, yesterday]))
            .order_by(
                IngestionProgress.day_utc.desc(),
                IngestionProgress.source_type,
                IngestionProgress.feed_name,
            )
            .all()
        )

        # Group by source type
        sources = {}
        problem_feeds = []

        for row in progress_rows:
            key = row.source_type
            if key not in sources:
                sources[key] = {
                    "source_type": row.source_type,
                    "feeds": [],
                    "total_ingested_today": 0,
                    "total_attempted_today": 0,
                    "feeds_complete": 0,
                    "feeds_failed": 0,
                    "feeds_running": 0,
                }

            feed_data = {
                "feed_name": row.feed_name,
                "day": row.day_utc.isoformat(),
                "status": row.status,
                "items_ingested": row.items_ingested,
                "items_attempted": row.items_attempted,
                "target": row.target,
                "retry_count": row.retry_count,
                "retry_at": row.retry_at.isoformat() if row.retry_at else None,
                "last_error": row.last_error[:200] if row.last_error else None,  # Truncate
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }

            # Only count today's stats for totals
            if row.day_utc == today:
                sources[key]["feeds"].append(feed_data)
                sources[key]["total_ingested_today"] += row.items_ingested
                sources[key]["total_attempted_today"] += row.items_attempted

                if row.status == "complete":
                    sources[key]["feeds_complete"] += 1
                elif row.status == "failed":
                    sources[key]["feeds_failed"] += 1
                elif row.status == "running":
                    sources[key]["feeds_running"] += 1

                # Track problem feeds
                if row.status == "failed" or row.retry_count >= 3:
                    problem_feeds.append(
                        {
                            "source_type": row.source_type,
                            "feed_name": row.feed_name,
                            "status": row.status,
                            "retry_count": row.retry_count,
                            "last_error": row.last_error[:200] if row.last_error else None,
                        }
                    )

        # Calculate success rates for each source
        for _key, data in sources.items():
            attempted = data["total_attempted_today"]
            if attempted > 0:
                data["success_rate"] = round(data["total_ingested_today"] / attempted * 100, 1)
            else:
                data["success_rate"] = None

        # Get source daily stats for historical context
        source_stats = (
            db.query(SourceDailyStat)
            .filter(SourceDailyStat.day.in_([today, yesterday]))
            .order_by(SourceDailyStat.day.desc(), SourceDailyStat.source)
            .all()
        )

        daily_stats = {}
        for stat in source_stats:
            key = stat.source
            if key not in daily_stats:
                daily_stats[key] = []
            daily_stats[key].append(
                {
                    "day": stat.day.isoformat(),
                    "inserted": stat.inserted,
                    "suppressed": stat.suppressed,
                }
            )

        demoted_channels = (
            db.query(VideoSourceProfile)
            .filter(
                VideoSourceProfile.enabled.is_(True),
                VideoSourceProfile.status.in_(["discovery", "blocked"]),
            )
            .all()
        )

        # ── Pool & freshness counts per content type ──────────────────────
        now_utc = datetime.now(timezone.utc)
        cutoff_7d = now_utc - timedelta(days=7)
        cutoff_24h = now_utc - timedelta(hours=24)

        pool_counts = {}
        recent_counts = {}
        for ct in (ContentType.ARTICLE, ContentType.VIDEO, ContentType.REEL):
            base_q = db.query(func.count(ContentItem.id)).filter(
                ContentItem.type == ct,
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.is_suppressed.is_(False),
            )
            pool_counts[ct.value] = (
                base_q.filter(ContentItem.published_at >= cutoff_7d).scalar() or 0
            )
            recent_counts[ct.value] = (
                base_q.filter(ContentItem.published_at >= cutoff_24h).scalar() or 0
            )

        # ── Curated vs discovery mix (video/reel channels) ───────────────
        curated_vs_discovery = {}
        for ct in (ContentType.VIDEO, ContentType.REEL):
            rows = (
                db.query(ContentItem.discovered_via, func.count(ContentItem.id))
                .filter(
                    ContentItem.type == ct,
                    ContentItem.curation_status == ContentStatus.PROMOTED,
                    ContentItem.is_suppressed.is_(False),
                    ContentItem.published_at >= cutoff_7d,
                )
                .group_by(ContentItem.discovered_via)
                .all()
            )
            breakdown = {}
            for lane, cnt in rows:
                bucket = _curation_mix_bucket(lane)
                breakdown[bucket] = breakdown.get(bucket, 0) + cnt
            curated_vs_discovery[ct.value] = breakdown

        # ── Source promotion / demotion actions (last 7d) ─────────────────
        status_actions = (
            db.query(
                VideoSourceProfile.status,
                func.count(VideoSourceProfile.channel_id),
            )
            .filter(
                VideoSourceProfile.status_changed_at >= cutoff_7d,
            )
            .group_by(VideoSourceProfile.status)
            .all()
        )
        promotion_demotion = {status: cnt for status, cnt in status_actions}

        # ── Language filtered count (lifetime) ────────────────────────────
        lang_counters = extraction_metrics.get_counters()
        language_filtered_total = lang_counters.get("content_filtered_language_total", 0)

        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "today": today.isoformat(),
            "sources": list(sources.values()),
            "source_daily_stats": daily_stats,
            "demoted_channels": [
                {
                    "channel_id": ch.channel_id,
                    "channel_name": ch.channel_name,
                    "status": ch.status,
                    "score_7d": ch.score_7d,
                    "promotion_rate_7d": ch.promotion_rate_7d,
                }
                for ch in sorted(demoted_channels, key=lambda c: c.score_7d or 0)
            ],
            "problem_feeds": problem_feeds,
            "pool_7d": pool_counts,
            "recent_24h": recent_counts,
            "curated_vs_discovery_mix": curated_vs_discovery,
            "source_promotion_demotion_actions": promotion_demotion,
            "language_filtered_count": language_filtered_total,
            "summary": {
                "total_sources": len(sources),
                "total_problem_feeds": len(problem_feeds),
                "sources_with_failures": sum(1 for s in sources.values() if s["feeds_failed"] > 0),
                "demoted_channel_count": len(demoted_channels),
            },
        }

    except Exception as e:
        logger.error(f"Error getting source health metrics: {e}", exc_info=True)
        return {
            "error": str(e),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/extraction", dependencies=[Depends(require_admin_key)])
def get_extraction_metrics() -> Dict[str, Any]:
    """
    Get content extraction pipeline metrics.

    Returns:
        - Global counters: ok/fallback/failed totals for extraction and images
        - Per-source health scores with fail rates
        - Degraded sources (health below threshold)

    Requires ADMIN_API_KEY.
    """
    counters = extraction_metrics.get_counters()
    source_health = extraction_metrics.get_source_health()

    threshold = getattr(settings, "SOURCE_HEALTH_DEGRADED_THRESHOLD", 0.3)
    degraded = [
        name for name in source_health if extraction_metrics.is_source_degraded(name, threshold)
    ]

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "counters": counters,
        "language_filtered_count": counters.get("content_filtered_language_total", 0),
        "source_health": source_health,
        "degraded_sources": degraded,
        "config": {
            "extraction_enabled": getattr(settings, "EXTRACTION_ENABLED", True),
            "connect_timeout": getattr(settings, "EXTRACTION_CONNECT_TIMEOUT", 10),
            "read_timeout": getattr(settings, "EXTRACTION_READ_TIMEOUT", 20),
            "domain_min_interval": getattr(settings, "EXTRACTION_DOMAIN_MIN_INTERVAL", 1.0),
            "health_degraded_threshold": threshold,
        },
    }


@router.get("/extraction/samples", dependencies=[Depends(require_admin_key)])
def get_extraction_samples(
    limit: int = Query(20, ge=1, le=100, description="Number of recent samples"),
) -> Dict[str, Any]:
    """
    Get recent extraction samples for debugging.

    Returns the last N extraction results with source_url, canonical_url,
    extraction_status, extractor_used, image_status, etc.

    Requires ADMIN_API_KEY.
    """
    samples = extraction_metrics.get_samples(limit=limit)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "count": len(samples),
        "samples": samples,
    }


@router.get("/ai-coverage", dependencies=[Depends(require_admin_key)])
def get_ai_coverage_metrics(
    hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Get AI content coverage metrics for the promoted feed.

    Returns ``ai_share_pct``, ``ai_cluster_hotness``, and
    ``ai_source_diversity`` for items promoted in the last *hours* hours,
    so operators can verify the 40 % AI cap is holding in production.

    Requires ADMIN_API_KEY.
    """
    try:
        from app.models.content import ContentItem, ContentStatus

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_naive = cutoff.replace(tzinfo=None)

        items = (
            db.query(ContentItem)
            .filter(
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.published_at >= cutoff_naive,
                ContentItem.is_suppressed.is_(False),
            )
            .order_by(ContentItem.published_at.desc())
            .all()
        )

        ai_metrics = compute_ai_feed_metrics(items)

        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "window_hours": hours,
            "items_analyzed": len(items),
            **ai_metrics,
            "cap_breached": ai_metrics["ai_share_pct"] > 40.0,
        }

    except Exception as exc:
        logger.error("Error getting AI coverage metrics: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/ai-usage", dependencies=[Depends(require_admin_key)])
def get_ai_usage_metrics(
    days: int = Query(7, ge=1, le=31, description="Look-back window in UTC days"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get model-wise LLM usage, token, estimated cost, and error metrics."""
    try:
        return compute_ai_usage_metrics(db, days=days)
    except Exception as exc:
        logger.error("Error getting AI usage metrics: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/inventory/health", dependencies=[Depends(require_admin_key)])
def get_inventory_health(
    hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Get category and source distribution for the promoted feed inventory.

    Returns per-category and per-source counts, share percentages, the
    dominant source, and a combined infra coverage figure so operators
    can detect single-source dominance (> 30 %) or thin infra coverage
    (< 10 %%).

    Requires ADMIN_API_KEY.
    """
    try:
        from app.models.content import ContentItem, ContentStatus

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_naive = cutoff.replace(tzinfo=None)

        items = (
            db.query(ContentItem)
            .filter(
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.published_at >= cutoff_naive,
                ContentItem.is_suppressed.is_(False),
            )
            .order_by(ContentItem.published_at.desc())
            .all()
        )

        health = compute_inventory_health(items)

        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "window_hours": hours,
            **health,
            "alerts": {
                "dominant_source_warning": health["dominant_source_pct"] > 30.0,
                "infra_coverage_low": health["infra_share_pct"] < 10.0,
            },
        }

    except Exception as exc:
        logger.error("Error getting inventory health metrics: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/signal", dependencies=[Depends(require_admin_key)])
def get_signal_metrics(
    hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get Coverage Guarantee and Quality Gate metrics.

    Returns:
    - signal_urls_seen / signal_urls_added (last N hours)
    - promote_runs, promoted_count (last N hours)
    - duplicate_ratio per source (signal URLs that were already in content_items)
    - top_sources_by_share: per-source contribution to the PROMOTED feed
    - pipeline_summary: CANDIDATE vs PROMOTED counts per content type

    Requires ADMIN_API_KEY.
    """
    try:
        from app.models.content import ContentItem, ContentStatus
        from app.models.signal import EnqueueStatus, SignalURL

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_naive = cutoff.replace(tzinfo=None)

        # ── Signal URL counters ───────────────────────────────────────────
        signal_seen = int(
            db.query(func.count(SignalURL.id))
            .filter(SignalURL.first_seen_at >= cutoff_naive)
            .scalar()
            or 0
        )
        signal_added = int(
            db.query(func.count(SignalURL.id))
            .filter(
                SignalURL.enqueue_status == EnqueueStatus.INGESTED,
                SignalURL.enqueued_at >= cutoff_naive,
            )
            .scalar()
            or 0
        )

        # Status breakdown
        status_rows = (
            db.query(SignalURL.enqueue_status, func.count(SignalURL.id))
            .filter(SignalURL.first_seen_at >= cutoff_naive)
            .group_by(SignalURL.enqueue_status)
            .all()
        )
        signal_by_status = {(row[0].value if row[0] else "unknown"): row[1] for row in status_rows}

        # Duplicate ratio per signal source
        source_stats_rows = (
            db.query(SignalURL.signal_source, SignalURL.enqueue_status, func.count(SignalURL.id))
            .filter(SignalURL.first_seen_at >= cutoff_naive)
            .group_by(SignalURL.signal_source, SignalURL.enqueue_status)
            .all()
        )
        source_totals: Dict[str, int] = {}
        source_dupes: Dict[str, int] = {}
        for row in source_stats_rows:
            src = row[0].value if row[0] else "unknown"
            status = row[1]
            cnt = row[2]
            source_totals[src] = source_totals.get(src, 0) + cnt
            if status == EnqueueStatus.DUPLICATE:
                source_dupes[src] = source_dupes.get(src, 0) + cnt

        duplicate_ratio = {
            src: round(source_dupes.get(src, 0) / max(total, 1), 3)
            for src, total in source_totals.items()
        }

        # ── Promotion pipeline counts ─────────────────────────────────────
        pipeline_rows = (
            db.query(ContentItem.type, ContentItem.curation_status, func.count(ContentItem.id))
            .filter(
                ContentItem.published_at >= cutoff_naive,
                ContentItem.is_suppressed.is_(False),
            )
            .group_by(ContentItem.type, ContentItem.curation_status)
            .all()
        )
        pipeline: Dict[str, Any] = {}
        for row in pipeline_rows:
            type_key = row[0].value.lower() if row[0] else "unknown"
            status_key = row[1].value.lower() if row[1] else "promoted"
            if type_key not in pipeline:
                pipeline[type_key] = {}
            pipeline[type_key][status_key] = row[2]

        # ── Top sources by share in PROMOTED feed ─────────────────────────
        source_share_rows = (
            db.query(ContentItem.source, func.count(ContentItem.id).label("cnt"))
            .filter(
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.published_at >= cutoff_naive,
                ContentItem.is_suppressed.is_(False),
            )
            .group_by(ContentItem.source)
            .order_by(func.count(ContentItem.id).desc())
            .limit(20)
            .all()
        )
        top_sources = [{"source": r[0], "promoted_count": r[1]} for r in source_share_rows]
        total_promoted = sum(r[1] for r in source_share_rows)
        for s in top_sources:
            s["share_pct"] = round(s["promoted_count"] / max(total_promoted, 1) * 100, 1)

        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "window_hours": hours,
            "signal": {
                "signal_urls_seen": signal_seen,
                "signal_urls_added": signal_added,
                "by_status": signal_by_status,
                "duplicate_ratio_by_source": duplicate_ratio,
            },
            "promotion": {
                "pipeline_counts": pipeline,
                "total_promoted_in_window": total_promoted,
            },
            "top_sources_by_share": top_sources,
        }

    except Exception as exc:
        logger.error("Error getting signal metrics: %s", exc, exc_info=True)
        return {
            "error": str(exc),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/freshness", dependencies=[Depends(require_admin_key)])
def get_freshness_metrics(
    hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get freshness analytics across serving, engagement, and top-up behavior."""
    try:
        return compute_freshness_metrics(db, hours=hours)
    except Exception as exc:
        logger.error("Error getting freshness metrics: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/articles/supply", dependencies=[Depends(require_admin_key)])
def get_article_supply_metrics(
    days: int = Query(14, ge=1, le=31, description="Look-back window in UTC days"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get per-day and per-source article supply breakdowns."""
    try:
        return compute_article_supply_metrics(db, days=days)
    except Exception as exc:
        logger.error("Error getting article supply metrics: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/content/strategic-health", dependencies=[Depends(require_admin_key)])
def get_strategic_content_health(
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get strategic content-health diagnostics used for operator alerting."""
    try:
        return compute_strategic_content_health(db)
    except Exception as exc:
        logger.error("Error getting strategic content health: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/video-supply", dependencies=[Depends(require_admin_key)])
def get_video_supply_metrics(
    baseline_tag: str | None = Query(
        None,
        description="Committed baseline tag to compare against",
    ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get supply, freshness, coverage, and baseline deltas for videos/reels."""
    try:
        return compute_video_supply_metrics(db, baseline_tag=baseline_tag)
    except Exception as exc:
        logger.error("Error getting video supply metrics: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/video-lanes", dependencies=[Depends(require_admin_key)])
def get_video_lane_metrics(
    hours: int = Query(24, ge=1, le=24 * 14, description="Look-back window in hours"),
    breakdown: str | None = Query(
        None,
        pattern="^(query)?$",
        description="Optional additive breakdown. Use 'query' for per-query metrics.",
    ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get discovery-lane candidate, rejection, and promotion metrics."""
    try:
        return compute_video_lane_metrics(db, hours=hours, breakdown=breakdown)
    except Exception as exc:
        logger.error("Error getting video lane metrics: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/video-sources", dependencies=[Depends(require_admin_key)])
def get_video_source_metrics(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get per-channel health and status for the admin portal."""
    try:
        return compute_video_source_metrics(db)
    except Exception as exc:
        logger.error("Error getting video source metrics: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/worker/lanes", dependencies=[Depends(require_admin_key)])
def get_worker_lane_metrics(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get single-worker lane heartbeats and durable outbox queue depth."""
    try:
        queue_rows = (
            db.query(
                ContentEventOutbox.event_type,
                ContentEventOutbox.status,
                func.count(ContentEventOutbox.id).label("count"),
                func.min(ContentEventOutbox.available_at).label("oldest_available_at"),
            )
            .filter(ContentEventOutbox.status.in_(("pending", "processing")))
            .group_by(ContentEventOutbox.event_type, ContentEventOutbox.status)
            .order_by(ContentEventOutbox.event_type, ContentEventOutbox.status)
            .all()
        )
        queue_depth = [
            {
                "event_type": row.event_type,
                "status": row.status,
                "count": int(row.count or 0),
                "oldest_available_at": (
                    row.oldest_available_at.isoformat() if row.oldest_available_at else None
                ),
            }
            for row in queue_rows
        ]
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "heartbeats": read_lane_heartbeats(),
            "outbox": queue_depth,
        }
    except Exception as exc:
        logger.error("Error getting worker lane metrics: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
