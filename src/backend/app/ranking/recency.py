"""
Recency score computation.

Recency score reflects how fresh content is, using exponential decay.

Formula:
    recency_score = 2^(-hours_old / half_life)
    
This gives:
    - 0 hours old → 1.0
    - half_life hours old → 0.5
    - 2 * half_life hours old → 0.25
    - etc.

Configuration is loaded from app.config.scoring.
"""

import math
from datetime import datetime, timezone
from typing import Optional

from app.config.scoring import recency_config


def compute_recency_score(
    published_at: datetime,
    reference_time: Optional[datetime] = None,
    half_life_hours: Optional[int] = None,
    max_age_hours: Optional[int] = None,
    min_score: Optional[float] = None,
) -> float:
    """
    Compute recency score using exponential decay.
    
    Args:
        published_at: When content was published
        reference_time: Time to measure against (default: now)
        half_life_hours: Hours until score drops to 50%
        max_age_hours: Maximum age to consider
        min_score: Minimum score floor
        
    Returns:
        Recency score between min_score and 1.0
    """
    # Use config defaults if not provided
    if half_life_hours is None:
        half_life_hours = recency_config.half_life_hours
    if max_age_hours is None:
        max_age_hours = recency_config.max_age_hours
    if min_score is None:
        min_score = recency_config.min_score
    
    # Ensure published_at is timezone-aware
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    
    # Use current time as reference if not provided
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)
    elif reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    
    # Calculate age in hours
    age_delta = reference_time - published_at
    hours_old = age_delta.total_seconds() / 3600
    
    # Handle future-dated content
    if hours_old < 0:
        return 1.0
    
    # Cap at max age
    if hours_old > max_age_hours:
        return min_score
    
    # Exponential decay: score = 2^(-hours_old / half_life)
    decay = -hours_old / half_life_hours
    score = math.pow(2, decay)
    
    # Apply floor
    return max(min_score, score)


def get_age_hours(
    published_at: datetime,
    reference_time: Optional[datetime] = None,
) -> float:
    """
    Get content age in hours.
    
    Args:
        published_at: When content was published
        reference_time: Time to measure against (default: now)
        
    Returns:
        Age in hours (can be negative for future content)
    """
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)
    elif reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    
    age_delta = reference_time - published_at
    return age_delta.total_seconds() / 3600


def is_fresh(
    published_at: datetime,
    threshold_hours: int = 24,
) -> bool:
    """
    Check if content is considered fresh.
    
    Args:
        published_at: When content was published
        threshold_hours: Hours to consider fresh
        
    Returns:
        True if content is younger than threshold
    """
    age = get_age_hours(published_at)
    return age < threshold_hours
