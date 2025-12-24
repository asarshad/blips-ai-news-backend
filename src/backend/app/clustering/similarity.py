"""
Content similarity functions.

Computes similarity between content items using:
- Entity overlap (Jaccard index)
- Title similarity (sequence matching)
- Topic overlap (Jaccard index)
"""

from typing import List, Dict
from difflib import SequenceMatcher

from app.config.clustering import clustering_config


def compute_similarity(
    item1_entities: List[Dict],
    item1_title: str,
    item1_topics: List[str],
    item2_entities: List[Dict],
    item2_title: str,
    item2_topics: List[str],
) -> float:
    """
    Compute overall similarity between two items.
    
    Uses weighted combination of:
    - Entity overlap (weight from config)
    - Title similarity (weight from config)
    - Topic overlap (weight from config)
    
    Args:
        item1_*: Attributes of first item
        item2_*: Attributes of second item
        
    Returns:
        Similarity score between 0 and 1
    """
    entity_score = compute_entity_overlap(item1_entities, item2_entities)
    title_score = compute_title_similarity(item1_title, item2_title)
    topic_score = compute_topic_overlap(item1_topics, item2_topics)
    
    combined = (
        clustering_config.entity_weight * entity_score +
        clustering_config.title_weight * title_score +
        clustering_config.topic_weight * topic_score
    )
    
    return min(1.0, max(0.0, combined))


def compute_entity_overlap(
    entities1: List[Dict],
    entities2: List[Dict],
) -> float:
    """
    Compute entity overlap using Jaccard index.
    
    Entities are dicts with 'name' key.
    
    Args:
        entities1: List of entity dicts from first item
        entities2: List of entity dicts from second item
        
    Returns:
        Jaccard index (0-1)
    """
    if not entities1 or not entities2:
        return 0.0
    
    # Extract normalized names
    names1 = {e.get("name", "").lower().strip() for e in entities1 if e.get("name")}
    names2 = {e.get("name", "").lower().strip() for e in entities2 if e.get("name")}
    
    if not names1 or not names2:
        return 0.0
    
    intersection = len(names1 & names2)
    union = len(names1 | names2)
    
    return intersection / union if union > 0 else 0.0


def compute_title_similarity(title1: str, title2: str) -> float:
    """
    Compute title similarity using sequence matching.
    
    Titles are normalized before comparison:
    - Lowercase
    - Common prefixes/suffixes removed
    
    Args:
        title1: First title
        title2: Second title
        
    Returns:
        Similarity ratio (0-1)
    """
    t1 = normalize_title(title1)
    t2 = normalize_title(title2)
    
    return SequenceMatcher(None, t1, t2).ratio()


def normalize_title(title: str) -> str:
    """
    Normalize title for comparison.
    
    Removes common news site prefixes/suffixes and converts to lowercase.
    """
    if not title:
        return ""
    
    title = title.lower().strip()
    
    # Common patterns to remove
    remove_patterns = [
        # Prefixes
        "breaking:",
        "update:",
        "exclusive:",
        "opinion:",
        "analysis:",
        "review:",
        "hands-on:",
        "watch:",
        "video:",
        # Site suffixes
        "| techcrunch",
        "| the verge",
        "| wired",
        "| ars technica",
        "| engadget",
        "| cnet",
        "- techcrunch",
        "- the verge",
        "- wired",
        "- ars technica",
        "- engadget",
        "- cnet",
    ]
    
    for pattern in remove_patterns:
        title = title.replace(pattern, "")
    
    return title.strip()


def compute_topic_overlap(
    topics1: List[str],
    topics2: List[str],
) -> float:
    """
    Compute topic overlap using Jaccard index.
    
    Args:
        topics1: List of topics from first item
        topics2: List of topics from second item
        
    Returns:
        Jaccard index (0-1)
    """
    if not topics1 or not topics2:
        return 0.0
    
    set1 = {t.lower().strip() for t in topics1}
    set2 = {t.lower().strip() for t in topics2}
    
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    
    return intersection / union if union > 0 else 0.0
