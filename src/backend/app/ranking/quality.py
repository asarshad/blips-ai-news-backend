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

from typing import Optional
from app.config.scoring import get_source_quality


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
        
    Returns:
        Quality score between 0.0 and 1.0
    """
    source_weight = compute_source_weight(source)
    completeness = compute_completeness_score(
        title, summary, description, image_url, topics, entities
    )
    
    # Source weight is more important than completeness
    quality_score = (source_weight * 0.70) + (completeness * 0.30)
    
    return min(1.0, max(0.0, quality_score))
