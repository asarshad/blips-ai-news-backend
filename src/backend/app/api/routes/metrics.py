"""
Metrics API endpoints for operational monitoring.

These endpoints provide detailed insights into system health and performance.
Includes extraction pipeline metrics and per-source health scoring.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import require_admin_key
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.extraction.metrics import extraction_metrics
from app.models.ingestion_progress import IngestionProgress
from app.models.source import SourceDailyStat

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
        # Get today's ingestion progress for all feeds
        progress_rows = (
            db.query(IngestionProgress)
            .filter(IngestionProgress.day_utc.in_([today, yesterday]))
            .order_by(IngestionProgress.day_utc.desc(), IngestionProgress.source_type, IngestionProgress.feed_name)
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
                    problem_feeds.append({
                        "source_type": row.source_type,
                        "feed_name": row.feed_name,
                        "status": row.status,
                        "retry_count": row.retry_count,
                        "last_error": row.last_error[:200] if row.last_error else None,
                    })
        
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
            daily_stats[key].append({
                "day": stat.day.isoformat(),
                "inserted": stat.inserted,
                "suppressed": stat.suppressed,
            })
        
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "today": today.isoformat(),
            "sources": list(sources.values()),
            "source_daily_stats": daily_stats,
            "problem_feeds": problem_feeds,
            "summary": {
                "total_sources": len(sources),
                "total_problem_feeds": len(problem_feeds),
                "sources_with_failures": sum(1 for s in sources.values() if s["feeds_failed"] > 0),
            }
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
        name
        for name in source_health
        if extraction_metrics.is_source_degraded(name, threshold)
    ]

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "counters": counters,
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
