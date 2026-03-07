"""
Deduplication utilities.

Provides functions for detecting duplicate content.
"""

import hashlib
import re
from typing import Optional

_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalize_title_for_hash(title: str) -> str:
    text = (title or "").lower().strip()
    if not text:
        return ""
    tokens = _TITLE_TOKEN_RE.findall(text)
    return " ".join(tokens)


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
    normalized_title = _normalize_title_for_hash(title)
    normalized_source = (source or "").lower().strip()

    if not normalized_title or not normalized_source:
        return ""

    normalized = f"{normalized_title}:{normalized_source}"
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


def compute_title_simhash(title: str, *, bits: int = 63) -> Optional[int]:
    """
    Compute a compact SimHash-style title signature for near-duplicate checks.

    Uses token-level hashing and weighted bit vectors.
    Returns a positive integer that fits into signed BIGINT when bits <= 63.
    """
    normalized = _normalize_title_for_hash(title)
    if not normalized:
        return None

    tokens = normalized.split()
    if not tokens:
        return None

    weights = [0] * bits
    for token in tokens:
        token_hash = int(hashlib.sha1(token.encode("utf-8")).hexdigest(), 16)
        for bit in range(bits):
            mask = 1 << bit
            weights[bit] += 1 if (token_hash & mask) else -1

    fingerprint = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            fingerprint |= 1 << bit

    return fingerprint


def hamming_distance(left: int, right: int) -> int:
    """Return Hamming distance between two integer fingerprints."""
    return (left ^ right).bit_count()


def is_near_duplicate_simhash(
    left: Optional[int], right: Optional[int], *, max_distance: int = 3
) -> bool:
    """
    Check whether two SimHash fingerprints are near-duplicates.
    """
    if left is None or right is None:
        return False
    return hamming_distance(int(left), int(right)) <= max_distance
