"""Admin routes for feature flag management.

These endpoints allow instant feature flag updates without redeploy.
Should be protected in production (e.g., behind internal network or auth).
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_admin_key
from app.core.feature_flags import FeatureFlags, get_feature_flags
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(require_admin_key)])


class FeatureFlagUpdate(BaseModel):
    """Request body for updating a feature flag."""
    enabled: bool


class FeatureFlagResponse(BaseModel):
    """Response for a single feature flag."""
    feature: str
    enabled: bool
    source: str


@router.get("/flags", response_model=Dict[str, Any])
def get_all_feature_flags(
    flags: FeatureFlags = Depends(get_feature_flags)
):
    """
    Get status of all feature flags.
    
    Returns dict with each feature's current status and source (redis/env/default).
    """
    return flags.get_all_flags()


@router.get("/flags/{feature}", response_model=FeatureFlagResponse)
def get_feature_flag(
    feature: str,
    flags: FeatureFlags = Depends(get_feature_flags)
):
    """
    Get status of a specific feature flag.
    
    Args:
        feature: Feature name (e.g., "chat", "ingestion")
    """
    all_flags = flags.get_all_flags()
    
    if feature.lower() not in all_flags:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown feature: {feature}. Known features: {list(all_flags.keys())}"
        )
    
    flag_info = all_flags[feature.lower()]
    return FeatureFlagResponse(
        feature=feature.lower(),
        enabled=flag_info["enabled"],
        source=flag_info["source"]
    )


@router.put("/flags/{feature}", response_model=FeatureFlagResponse)
def update_feature_flag(
    feature: str,
    update: FeatureFlagUpdate,
    flags: FeatureFlags = Depends(get_feature_flags)
):
    """
    Update a feature flag (stored in Redis for instant effect).
    
    Args:
        feature: Feature name (e.g., "chat", "ingestion")
        update: New enabled status
    """
    feature = feature.lower()
    
    success = flags.set_flag(feature, update.enabled)
    if not success:
        raise HTTPException(
            status_code=500,
            detail="Failed to update feature flag (Redis unavailable)"
        )
    
    logger.info(f"Feature flag '{feature}' updated to {update.enabled}")
    
    return FeatureFlagResponse(
        feature=feature,
        enabled=update.enabled,
        source="redis"
    )


@router.delete("/flags/{feature}")
def reset_feature_flag(
    feature: str,
    flags: FeatureFlags = Depends(get_feature_flags)
):
    """
    Reset a feature flag to its default value (removes Redis override).
    
    Args:
        feature: Feature name
    """
    feature = feature.lower()
    
    success = flags.delete_flag(feature)
    if not success:
        raise HTTPException(
            status_code=500,
            detail="Failed to reset feature flag (Redis unavailable)"
        )
    
    # Get the new value (will be from env or default)
    new_value = flags.is_enabled(feature)
    
    logger.info(f"Feature flag '{feature}' reset to default ({new_value})")
    
    return {
        "feature": feature,
        "enabled": new_value,
        "source": "env" if flags._get_from_env(feature) is not None else "default",
        "message": "Feature flag reset to default"
    }


# =============================================================================
# Manual Triggers (Dev/Testing)
# =============================================================================

@router.get("/stats/content")
def get_content_stats():
    """Get content statistics for monitoring."""
    from sqlalchemy import text

    from app.db.base import SessionLocal
    
    db = SessionLocal()
    try:
        # Count by type
        result = db.execute(text("""
            SELECT type, COUNT(*) as count
            FROM content_items
            GROUP BY type
        """))
        
        by_type = {row[0]: row[1] for row in result}
        
        # Count duplicates
        result = db.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT source_url
                FROM content_items
                GROUP BY source_url
                HAVING COUNT(*) > 1
            ) AS duplicates
        """))
        duplicate_count = result.scalar() or 0
        
        # Recent 24h
        result = db.execute(text("""
            SELECT type, COUNT(*) as count
            FROM content_items
            WHERE created_at > NOW() - INTERVAL '24 hours'
            GROUP BY type
        """))
        recent_24h = {row[0]: row[1] for row in result}
        
        return {
            "total_by_type": by_type,
            "total": sum(by_type.values()),
            "duplicate_urls": duplicate_count,
            "last_24h": recent_24h
        }
        
    finally:
        db.close()


@router.post("/trigger-fetch")
def trigger_fetch():
    """Manually trigger content ingestion (dev/testing only)."""
    from app.scheduler.tasks import fetch_and_process_news
    
    try:
        fetch_and_process_news()
        return {"status": "triggered", "message": "Ingestion job triggered successfully"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ------------------------------------------------------------------
# Maintenance / Retention endpoints
# ------------------------------------------------------------------

@router.post("/maintenance/cleanup")
def manual_cleanup():
    """
    Manually trigger data retention cleanup.

    Uses the same retention service as the daily scheduler.
    Returns counts of deleted rows per table.
    """
    from app.scheduler.tasks_cleanup import run_data_cleanup_job

    try:
        result = run_data_cleanup_job()
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.error(f"Manual cleanup failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/maintenance/status")
def cleanup_status():
    """
    Return last cleanup run info and current retention config.
    """
    from app.core.config import settings as _settings

    last_run_at = None
    try:
        from app.core.dependencies import get_redis
        r = get_redis()
        raw = r.get("blips:cleanup:last_run_at")
        if raw:
            last_run_at = raw.decode() if isinstance(raw, bytes) else raw
    except Exception:
        pass

    return {
        "last_run_at": last_run_at,
        "retention_policy": {
            "content_items_days": _settings.RETAIN_CONTENT_DAYS,
            "ingestion_progress_days": _settings.RETAIN_INGESTION_PROGRESS_DAYS,
            "events_days": _settings.RETAIN_EVENTS_DAYS,
            "conversations_days": _settings.RETAIN_CONVERSATIONS_DAYS,
            "usage_days": _settings.RETAIN_USAGE_DAYS,
            "editorial_actions_days": _settings.RETAIN_EDITORIAL_DAYS,
        },
    }

