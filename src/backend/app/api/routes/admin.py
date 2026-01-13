"""Admin routes for feature flag management.

These endpoints allow instant feature flag updates without redeploy.
Should be protected in production (e.g., behind internal network or auth).
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any

from app.core.feature_flags import get_feature_flags, FeatureFlags
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


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
