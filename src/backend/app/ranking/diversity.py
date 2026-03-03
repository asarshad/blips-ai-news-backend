"""
Diversity boost/penalty computation.

Diversity scoring prevents any single topic from dominating the feed.

When a topic exceeds the configured dominance threshold (e.g., 40%),
content in that topic receives a penalty to allow other topics to surface.

Formula:
    if topic_share > max_dominance:
        diversity_boost = -penalty_factor * (topic_share - max_dominance)
    else:
        diversity_boost = 0

The penalty is proportional to how much the topic exceeds the threshold.
"""

from collections import Counter
from typing import Dict, List, Optional

from app.config.scoring import diversity_config


def compute_topic_distribution(
    items: List[Dict[str, any]],
    topic_field: str = "topics",
) -> Dict[str, float]:
    """
    Compute topic distribution across a set of items.

    Args:
        items: List of content items with topics
        topic_field: Field name containing topics list

    Returns:
        Dictionary mapping topic -> share (0-1)
    """
    if not items:
        return {}

    topic_counts: Counter = Counter()
    total_topics = 0

    for item in items:
        topics = item.get(topic_field, []) or []
        for topic in topics:
            if isinstance(topic, dict):
                topic_name = topic.get("name", "").lower()
            else:
                topic_name = str(topic).lower()

            if topic_name:
                topic_counts[topic_name] += 1
                total_topics += 1

    if total_topics == 0:
        return {}

    return {topic: count / total_topics for topic, count in topic_counts.items()}


def get_dominant_topic(
    topic_distribution: Dict[str, float],
) -> Optional[tuple]:
    """
    Find the most dominant topic and its share.

    Args:
        topic_distribution: Topic -> share mapping

    Returns:
        Tuple of (topic_name, share) or None if empty
    """
    if not topic_distribution:
        return None

    max_topic = max(topic_distribution.items(), key=lambda x: x[1])
    return max_topic


def compute_diversity_boost(
    item_topics: List[str],
    topic_distribution: Dict[str, float],
    max_dominance: Optional[float] = None,
    penalty_factor: Optional[float] = None,
) -> float:
    """
    Compute diversity boost/penalty for a content item.

    Items in over-represented topics receive a penalty.
    Items in under-represented topics receive no bonus (neutral).

    Args:
        item_topics: Topics of the content item
        topic_distribution: Current topic distribution in feed
        max_dominance: Maximum allowed topic share
        penalty_factor: How harsh the penalty is

    Returns:
        Diversity modifier (typically -0.5 to 0)
    """
    if max_dominance is None:
        max_dominance = diversity_config.max_topic_dominance
    if penalty_factor is None:
        penalty_factor = diversity_config.penalty_factor

    if not item_topics or not topic_distribution:
        return 0.0

    # Normalize item topics
    normalized_topics = [
        t.lower() if isinstance(t, str) else t.get("name", "").lower() for t in item_topics
    ]

    # Find the most over-represented topic this item belongs to
    max_excess = 0.0
    for topic in normalized_topics:
        share = topic_distribution.get(topic, 0.0)
        if share > max_dominance:
            excess = share - max_dominance
            max_excess = max(max_excess, excess)

    if max_excess <= 0:
        return 0.0

    # Apply penalty proportional to excess
    # Scaled so 20% excess (e.g., 60% when max is 40%) gives full penalty
    penalty = -penalty_factor * (max_excess / 0.20)

    return max(-penalty_factor, penalty)


def should_include_for_diversity(
    item_topics: List[str],
    topic_distribution: Dict[str, float],
    max_dominance: Optional[float] = None,
) -> bool:
    """
    Check if item should be included based on diversity rules.

    Hard filter for extreme cases where a topic is way over-represented.

    Args:
        item_topics: Topics of the content item
        topic_distribution: Current topic distribution
        max_dominance: Maximum allowed topic share

    Returns:
        True if item should be included
    """
    if max_dominance is None:
        max_dominance = diversity_config.max_topic_dominance

    if not item_topics or not topic_distribution:
        return True

    # Allow 50% overshoot before hard filtering
    hard_limit = max_dominance * 1.5

    for topic in item_topics:
        topic_name = topic.lower() if isinstance(topic, str) else topic.get("name", "").lower()
        share = topic_distribution.get(topic_name, 0.0)
        if share > hard_limit:
            return False

    return True
