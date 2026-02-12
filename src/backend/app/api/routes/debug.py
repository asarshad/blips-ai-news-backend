"""
Debug endpoints for diagnosing feed freshness issues.

Provides instrumentation to identify whether stale data originates from:
- Redis cache
- Database query
- Mobile device cache

SECURITY NOTE: These endpoints should be disabled or auth-protected in production.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.core.auth import require_admin_key
from app.core.dependencies import get_db
from app.core.logging import get_logger
from app.models.content import ContentItem, ContentType
from app.services.inventory_service import (
    Surface,
    _get_surface_config,
    compute_inventory_health,
)
from app.services.tiered_feed_service import (
    TIERED_FEED_CACHE_TTL,
    _cache_key,
    _get_redis_client,
)

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(require_admin_key)])


def _surface_from_string(surface: str) -> Surface:
    """Convert string to Surface enum."""
    mapping = {
        "articles": Surface.ARTICLES,
        "videos": Surface.VIDEOS,
        "reels": Surface.REELS,
    }
    return mapping.get(surface.lower(), Surface.ARTICLES)


def _content_type_from_surface(surface: Surface) -> ContentType:
    """Map surface to content type."""
    return {
        Surface.ARTICLES: ContentType.ARTICLE,
        Surface.VIDEOS: ContentType.VIDEO,
        Surface.REELS: ContentType.REEL,
    }[surface]


@router.get("/feed_state")
def get_feed_state(
    surface: str = Query("articles", description="Surface: articles, videos, reels"),
    limit: int = Query(20, description="Limit for cache key lookup"),
    offset: int = Query(0, description="Offset for cache key lookup"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Get comprehensive feed state for debugging staleness issues.
    
    Returns:
        - newest_published_at: Most recent published_at in DB
        - newest_created_at: Most recent created_at in DB  
        - redis_cache_state: TTL, cached_at, data preview
        - tier_config: Current window settings
        - inventory_health: Current inventory status
    """
    now = datetime.utcnow()
    surface_enum = _surface_from_string(surface)
    content_type = _content_type_from_surface(surface_enum)
    cfg = _get_surface_config(surface_enum)
    
    # =========================================================================
    # Database state
    # =========================================================================
    # Get newest dates in DB for this content type
    newest_in_db = db.query(
        func.max(ContentItem.published_at).label("newest_published"),
        func.max(ContentItem.created_at).label("newest_created"),
        func.count(ContentItem.id).label("total_count"),
    ).filter(
        ContentItem.type == content_type,
        ContentItem.is_suppressed.is_(False),
    ).first()
    
    # Get items in fresh window
    fresh_cutoff = now - timedelta(hours=cfg["fresh_hours"])
    fresh_count = db.query(func.count(ContentItem.id)).filter(
        ContentItem.type == content_type,
        ContentItem.is_suppressed.is_(False),
        ContentItem.ai_processed.is_(True),
        ContentItem.published_at >= fresh_cutoff,
    ).scalar()
    
    # Get newest 5 items for preview
    newest_items = db.query(ContentItem).filter(
        ContentItem.type == content_type,
        ContentItem.is_suppressed.is_(False),
    ).order_by(
        desc(ContentItem.published_at)
    ).limit(5).all()
    
    db_state = {
        "newest_published_at": newest_in_db.newest_published.isoformat() if newest_in_db.newest_published else None,
        "newest_created_at": newest_in_db.newest_created.isoformat() if newest_in_db.newest_created else None,
        "total_count": newest_in_db.total_count,
        "fresh_count": fresh_count,
        "fresh_window_hours": cfg["fresh_hours"],
        "fresh_cutoff": fresh_cutoff.isoformat(),
        "newest_items_preview": [
            {
                "id": item.id,
                "title": item.title[:50] + "..." if len(item.title) > 50 else item.title,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "source": item.source,
            }
            for item in newest_items
        ],
    }
    
    # Calculate staleness
    if newest_in_db.newest_published:
        db_staleness_seconds = int((now - newest_in_db.newest_published).total_seconds())
        db_staleness_hours = db_staleness_seconds / 3600
    else:
        db_staleness_seconds = None
        db_staleness_hours = None
    
    db_state["staleness_seconds"] = db_staleness_seconds
    db_state["staleness_hours"] = round(db_staleness_hours, 2) if db_staleness_hours else None
    
    # =========================================================================
    # Redis cache state
    # =========================================================================
    redis_client = _get_redis_client()
    redis_state = {
        "available": redis_client is not None,
        "cache_key": None,
        "ttl_seconds": None,
        "cached_at": None,
        "cached_items_count": None,
        "cached_newest_published": None,
        "cache_hit": False,
    }
    
    if redis_client:
        cache_key = _cache_key(surface_enum, limit, offset, True)
        redis_state["cache_key"] = cache_key
        
        try:
            ttl = redis_client.ttl(cache_key)
            redis_state["ttl_seconds"] = ttl if ttl > 0 else None
            
            cached = redis_client.get(cache_key)
            if cached:
                import json
                data = json.loads(cached)
                items = data.get("items", [])
                redis_state["cache_hit"] = True
                redis_state["cached_items_count"] = len(items)
                
                # Find newest published_at in cached data
                if items:
                    cached_dates = [
                        item.get("published_at")
                        for item in items
                        if item.get("published_at")
                    ]
                    if cached_dates:
                        redis_state["cached_newest_published"] = max(cached_dates)
                        
                    # Preview first 3 cached items
                    redis_state["cached_items_preview"] = [
                        {
                            "id": item.get("id"),
                            "title": item.get("title", "")[:50],
                            "published_at": item.get("published_at"),
                            "freshness_tier": item.get("freshness_tier"),
                        }
                        for item in items[:3]
                    ]
                
                # Calculate cache age (TTL starts at TIERED_FEED_CACHE_TTL)
                if ttl and ttl > 0:
                    cache_age = TIERED_FEED_CACHE_TTL - ttl
                    redis_state["cache_age_seconds"] = cache_age
        except Exception as e:
            redis_state["error"] = str(e)
    
    # =========================================================================
    # Current tier configuration
    # =========================================================================
    tier_config = {
        "fresh_hours": cfg["fresh_hours"],
        "backfill_hours": cfg["backfill_hours"],
        "evergreen_days": cfg["evergreen_days"],
        "min_fresh": cfg["min_fresh"],
        "reservoir": cfg["reservoir"],
        "cache_ttl_seconds": TIERED_FEED_CACHE_TTL,
    }
    
    # =========================================================================
    # Inventory health
    # =========================================================================
    try:
        health = compute_inventory_health(db, now)
        surface_health = health.surfaces.get(surface_enum)
        inventory_status = {
            "is_healthy": health.is_healthy,
            "needs_topup": health.needs_topup,
            "surface_tier_a_count": surface_health.tier_counts.tier_a if surface_health else None,
            "surface_tier_b_count": surface_health.tier_counts.tier_b if surface_health else None,
            "surface_tier_c_count": surface_health.tier_counts.tier_c if surface_health else None,
        }
    except Exception as e:
        inventory_status = {"error": str(e)}
    
    # =========================================================================
    # Diagnosis
    # =========================================================================
    diagnosis = []
    
    if db_staleness_hours and db_staleness_hours > cfg["fresh_hours"]:
        diagnosis.append(f"DB_STALE: Newest published_at is {db_staleness_hours:.1f}h old (fresh window is {cfg['fresh_hours']}h)")
    
    if redis_state["cache_hit"]:
        if redis_state.get("cached_newest_published") and db_state["newest_published_at"]:
            if redis_state["cached_newest_published"] < db_state["newest_published_at"]:
                diagnosis.append("CACHE_STALE: Redis cache has older data than DB")
    elif redis_client:
        diagnosis.append("CACHE_MISS: No cached data, will query DB")
    
    if not redis_client:
        diagnosis.append("REDIS_UNAVAILABLE: No Redis connection, all requests hit DB")
    
    if fresh_count == 0:
        diagnosis.append("NO_FRESH_CONTENT: Zero items in Tier A fresh window")
    elif fresh_count < cfg["min_fresh"]:
        diagnosis.append(f"LOW_INVENTORY: Only {fresh_count} fresh items (min is {cfg['min_fresh']})")
    
    if not diagnosis:
        diagnosis.append("OK: Feed state looks healthy")
    
    return {
        "timestamp": now.isoformat(),
        "surface": surface,
        "db_state": db_state,
        "redis_state": redis_state,
        "tier_config": tier_config,
        "inventory_status": inventory_status,
        "diagnosis": diagnosis,
    }


@router.post("/invalidate_cache")
def invalidate_feed_cache(
    surface: Optional[str] = Query(None, description="Surface to invalidate, or all if not specified"),
) -> Dict[str, Any]:
    """Force invalidate feed cache for debugging."""
    from app.services.tiered_feed_service import invalidate_tiered_feed_cache
    
    if surface:
        surface_enum = _surface_from_string(surface)
        invalidate_tiered_feed_cache(surface_enum)
        return {"status": "ok", "invalidated": surface}
    else:
        invalidate_tiered_feed_cache()
        return {"status": "ok", "invalidated": "all"}


@router.post("/trigger_ai_processing")
def trigger_ai_processing(
    limit: int = Query(20, description="Max items to process"),
) -> Dict[str, Any]:
    """
    Manually trigger AI processing for unprocessed content.
    
    Use this to immediately process new articles instead of waiting
    for the scheduled job (every 15 minutes).
    """
    from app.scheduler.tasks_ai_retry import retry_ai_processing
    
    try:
        # Run the AI processing task synchronously
        retry_ai_processing()
        return {"status": "ok", "message": f"AI processing triggered (limit={limit})"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/cache_keys")
def list_cache_keys() -> Dict[str, Any]:
    """List all tiered feed cache keys for debugging."""
    redis_client = _get_redis_client()
    if not redis_client:
        return {"error": "Redis not available"}
    
    try:
        keys = redis_client.keys("blips:tiered_feed:*")
        key_info = []
        
        for key in keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            ttl = redis_client.ttl(key)
            key_info.append({
                "key": key_str,
                "ttl_seconds": ttl if ttl > 0 else None,
            })
        
        return {
            "count": len(keys),
            "keys": key_info,
        }
    except Exception as e:
        return {"error": str(e)}
