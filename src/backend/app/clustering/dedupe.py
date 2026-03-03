"""
Deduplication utilities.

Provides functions for detecting duplicate content.
"""

import hashlib


def compute_dedupe_key(title: str, source: str) -> str:
    """
    Compute a deduplication key for content.

    Uses normalized title + source to detect exact duplicates.
    This is used during ingestion to prevent inserting the same
    content multiple times.

    Args:
        title: Content title
        source: Content source name

    Returns:
        16-character hex hash

    Example:
        >>> compute_dedupe_key("Apple announces new iPhone", "TechCrunch")
        'a1b2c3d4e5f67890'
    """
    if not title or not source:
        return ""

    normalized = f"{title.lower().strip()}:{source.lower().strip()}"
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


def compute_content_hash(
    title: str,
    description: str = "",
    url: str = "",
) -> str:
    """
    Compute a content hash for deeper deduplication.

    Useful for catching duplicates with slightly different sources.

    Args:
        title: Content title
        description: Content description
        url: Content URL

    Returns:
        32-character hex hash
    """
    parts = [
        title.lower().strip() if title else "",
        description[:200].lower().strip() if description else "",
        url.lower().strip() if url else "",
    ]

    combined = "|".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:32]
