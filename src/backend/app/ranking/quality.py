"""
Quality score computation.

Quality score reflects the inherent trustworthiness and completeness
of a content item based on its source and metadata.

Formula:
    quality_score = (
        source_weight * 0.70 +
        completeness_score * 0.30
    )

Where:
    - source_weight: Pre-defined quality rating for the source (0-1)
    - completeness_score: How complete the content metadata is (0-1)
"""

import re
from typing import Optional

from app.config.scoring import get_source_quality

_LISTICLE_RE = re.compile(
    r"^\d+\s+(?:best|top|reasons|tips|gadgets|fitness)\b",
    re.IGNORECASE,
)
LISTICLE_PENALTY = 0.15


def listicle_penalty(title: str) -> float:
    """Return -LISTICLE_PENALTY for listicle titles, else 0.0."""
    if title and _LISTICLE_RE.match(title.strip()):
        return -LISTICLE_PENALTY
    return 0.0


def compute_source_weight(source: str) -> float:
    """
    Get quality weight for a source.

    Args:
        source: Content source name

    Returns:
        Quality weight between 0.0 and 1.0
    """
    return get_source_quality(source)


def compute_completeness_score(
    title: Optional[str],
    summary: Optional[str],
    description: Optional[str],
    image_url: Optional[str],
    topics: Optional[list],
    entities: Optional[list],
) -> float:
    """
    Compute completeness score based on available metadata.

    Checks presence of key fields and rewards completeness.

    Args:
        title: Content title
        summary: AI-generated summary
        description: Content description
        image_url: Preview image URL
        topics: Extracted topics
        entities: Extracted entities

    Returns:
        Completeness score between 0.0 and 1.0
    """
    score = 0.0
    max_score = 0.0

    # Title (required, heavily weighted)
    max_score += 0.25
    if title and len(title) > 10:
        score += 0.25

    # Summary (important for feed display)
    max_score += 0.25
    if summary and len(summary) > 50:
        score += 0.25
    elif summary and len(summary) > 20:
        score += 0.15

    # Description (secondary)
    max_score += 0.15
    if description and len(description) > 20:
        score += 0.15

    # Image (visual appeal)
    max_score += 0.15
    if image_url:
        score += 0.15

    # Topics (discoverability)
    max_score += 0.10
    if topics and len(topics) >= 2:
        score += 0.10
    elif topics and len(topics) >= 1:
        score += 0.05

    # Entities (clustering, related content)
    max_score += 0.10
    if entities and len(entities) >= 2:
        score += 0.10
    elif entities and len(entities) >= 1:
        score += 0.05

    return score / max_score if max_score > 0 else 0.0


def compute_quality_score(
    source: str,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    image_url: Optional[str] = None,
    topics: Optional[list] = None,
    entities: Optional[list] = None,
    tech_relevance_confidence: Optional[float] = None,
) -> float:
    """
    Compute overall quality score for content.

    Combines source reputation with metadata completeness.

    Args:
        source: Content source name
        title: Content title
        summary: AI-generated summary
        description: Content description
        image_url: Preview image URL
        topics: Extracted topics
        entities: Extracted entities
        tech_relevance_confidence: AI classifier confidence (0.0–1.0). Items
            with confidence below 0.80 are scaled down proportionally.
            At confidence=0.65: factor=0.8125 (−19%).
            At confidence=0.50: factor=0.625 (−38%).
            None means no confidence stored — no penalty applied.

    Returns:
        Quality score between 0.0 and 1.0
    """
    source_weight = compute_source_weight(source)
    completeness = compute_completeness_score(
        title, summary, description, image_url, topics, entities
    )

    # Source weight is more important than completeness
    quality_score = (source_weight * 0.70) + (completeness * 0.30)

    # Soft confidence multiplier: content below 0.80 confidence is scaled down.
    # At confidence=0.80+: factor=1.0 (no change)
    # At confidence=0.65: factor = 0.65/0.80 = 0.8125 (−19%)
    # At confidence=0.50: factor = 0.50/0.80 = 0.625 (−38%)
    # None (no confidence stored): factor=1.0 (no change)
    if tech_relevance_confidence is not None and tech_relevance_confidence < 0.80:
        quality_score *= tech_relevance_confidence / 0.80

    quality_score += listicle_penalty(title)

    return min(1.0, max(0.0, quality_score))
