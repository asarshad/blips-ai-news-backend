"""
Source dominance penalty computation.

Prevents any single source from monopolising the ranked feed.

At feed-composition time, the caller builds a source_counts dict
(source → number of times source appears in the N-item look-back window).
Items from a source whose share exceeds MAX_SOURCE_PCT receive a
proportional penalty.

Formula:
    share = source_count / window_size
    if share > MAX_SOURCE_PCT:
        excess = (share - MAX_SOURCE_PCT) / (1 - MAX_SOURCE_PCT)  # normalised 0→1
        dominance_penalty = excess * DOMINANCE_WEIGHT
    else:
        dominance_penalty = 0.0

Design notes:
- MAX_SOURCE_PCT = 0.30 means a source can appear in up to 30% of the
  window without penalty.  The 31st% starts the penalty ramp.
- DOMINANCE_WEIGHT caps the maximum penalty at 0.10, keeping it in the
  same ballpark as the interest_boost (0.10) so the two signals balance.
- Does NOT hard-block any source — diversity_mixer handles hard caps.
"""

import os
from typing import Dict

MAX_SOURCE_PCT: float = float(os.getenv("DOMINANCE_MAX_SOURCE_PCT", "0.30"))
DOMINANCE_WEIGHT: float = float(os.getenv("DOMINANCE_WEIGHT", "0.10"))


def compute_source_dominance_penalty(
    source: str,
    source_counts: Dict[str, int],
    window_size: int = 10,
) -> float:
    """
    Compute the dominance penalty for a content item.

    Args:
        source: The source name of the candidate item.
        source_counts: Mapping of source → count in the current ranking window.
            Should include the candidate item (i.e., count already incremented).
        window_size: Size of the look-back window used to compute share.

    Returns:
        Penalty to subtract from the personalised score (>= 0).
    """
    if window_size <= 0:
        return 0.0

    count = source_counts.get(source, 0)
    share = count / window_size

    if share <= MAX_SOURCE_PCT:
        return 0.0

    # Normalise excess to [0, 1]: how far over the cap are we?
    excess = (share - MAX_SOURCE_PCT) / max(1.0 - MAX_SOURCE_PCT, 1e-6)
    excess = max(0.0, min(1.0, excess))
    return excess * DOMINANCE_WEIGHT


def build_source_counts(items) -> Dict[str, int]:
    """
    Build a source_counts dict from an iterable of content items.

    Works with both SQLAlchemy models (item.source) and plain dicts
    (item["source"]).

    Args:
        items: Iterable of content items that have been selected so far.

    Returns:
        Dict mapping source name → count.
    """
    counts: Dict[str, int] = {}
    for item in items:
        src = item.source if hasattr(item, "source") else item.get("source", "")
        counts[src] = counts.get(src, 0) + 1
    return counts
