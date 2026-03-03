"""
Trend score computation.

Trend score reflects how "hot" or viral content is based on:
1. Cluster size (many sources covering same story)
2. Engagement signals (user interactions)

Formula:
    trend_score = (
        cluster_weight * cluster_score +
        engagement_weight * engagement_score
    )

Note: Engagement weight should be kept low (<30%) in early-stage products
to prevent gaming and ensure quality content surfaces.
"""

from typing import Dict, List, Optional

from app.config.scoring import get_engagement_weight, trend_config


def compute_cluster_score(
    cluster_size: int,
    max_cluster_size: int = 10,
) -> float:
    """
    Compute trend signal from cluster size.
    
    Larger clusters indicate more sources covering the same story,
    which suggests it's a significant/trending topic.
    
    Uses logarithmic scaling to prevent massive clusters from
    completely dominating.
    
    Args:
        cluster_size: Number of items in the cluster
        max_cluster_size: Size that gives maximum score
        
    Returns:
        Cluster score between 0.0 and 1.0
    """
    if cluster_size <= 1:
        return 0.0
    
    # Logarithmic scaling: log2(size) / log2(max)
    import math
    normalized = math.log2(cluster_size) / math.log2(max_cluster_size)
    
    return min(1.0, max(0.0, normalized))


def compute_engagement_score(
    events: List[Dict[str, any]],
    _time_window_hours: int = 24,
) -> float:
    """
    Compute trend signal from user engagement.
    
    Aggregates weighted engagement events to produce a score.
    
    Args:
        events: List of engagement events with 'type' and 'weight'
        time_window_hours: Only consider events within this window
        
    Returns:
        Engagement score between 0.0 and 1.0
    """
    if not events:
        return 0.0
    
    # Sum weighted events
    total_weight = 0
    for event in events:
        event_type = event.get("type")
        # Handle both string types and enum-like objects
        if hasattr(event_type, 'value'):
            weight = get_engagement_weight(event_type.value)
        elif isinstance(event_type, str):
            weight = get_engagement_weight(event_type)
        else:
            weight = event.get("weight", 1)
        total_weight += weight
    
    # Normalize: 50 weighted points = 1.0
    # This is a tunable constant
    normalization_factor = 50
    score = total_weight / normalization_factor
    
    return min(1.0, max(0.0, score))


def compute_trend_score(
    cluster_size: int = 1,
    engagement_events: Optional[List[Dict[str, any]]] = None,
    cluster_weight: Optional[float] = None,
    engagement_weight: Optional[float] = None,
) -> float:
    """
    Compute overall trend score for content.
    
    Combines cluster size signal with engagement signals.
    
    Args:
        cluster_size: Number of items covering same story
        engagement_events: List of user engagement events
        cluster_weight: Weight for cluster score (default from config)
        engagement_weight: Weight for engagement score (default from config)
        
    Returns:
        Trend score between 0.0 and 1.0
    """
    if cluster_weight is None:
        cluster_weight = trend_config.cluster_weight
    if engagement_weight is None:
        engagement_weight = trend_config.engagement_weight
    
    cluster_score = compute_cluster_score(cluster_size)
    engagement_score = compute_engagement_score(engagement_events or [])
    
    trend = (
        cluster_weight * cluster_score +
        engagement_weight * engagement_score
    )
    
    return min(1.0, max(0.0, trend))
