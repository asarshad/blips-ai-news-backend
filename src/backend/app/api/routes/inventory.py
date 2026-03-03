"""
Inventory Health API routes.

Provides visibility into content inventory across freshness tiers.
These endpoints are fast and do not trigger ingestion.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.logging import get_logger
from app.services.inventory_service import (
    Surface,
    compute_surface_health,
    get_cached_inventory_health,
    get_pipeline_counts,
)

logger = get_logger(__name__)
router = APIRouter()


@router.get("/health", response_model=Dict[str, Any])
def get_inventory_health(
    db: Session = Depends(get_db),
):
    """
    Get overall inventory health across all surfaces.

    Returns tier counts, freshness metrics, and health status for:
    - Articles
    - Videos
    - Reels

    Also includes the two-tier pipeline summary (CANDIDATE vs PROMOTED counts
    and newest timestamps per type) for operational visibility.

    Includes top-up priority if any surface is below threshold.
    This endpoint is cached (60s TTL) and does not trigger ingestion.
    """
    health = get_cached_inventory_health(db)
    response = health.to_dict()

    # Append pipeline (CANDIDATE vs PROMOTED) counts
    try:
        response["pipeline"] = get_pipeline_counts(db)
    except Exception as exc:
        logger.warning("Failed to compute pipeline counts: %s", exc)
        response["pipeline"] = {"error": str(exc)}

    return response


@router.get("/health/{surface}", response_model=Dict[str, Any])
def get_surface_health(
    surface: str,
    db: Session = Depends(get_db),
):
    """
    Get inventory health for a specific surface.

    Args:
        surface: One of 'articles', 'videos', 'reels'

    Returns tier counts, freshness metrics, and source distribution.
    """
    try:
        surf = Surface(surface.lower())
    except ValueError:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail=f"Invalid surface '{surface}'. Must be one of: articles, videos, reels",
        ) from None

    health = compute_surface_health(db, surf)
    return health.to_dict()
