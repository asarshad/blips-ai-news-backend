"""
Staleness decay computation.

Provides an explicit staleness *penalty* for the personalised feed score.
This is conceptually the inverse of recency, expressed as a subtraction.

While recency_score is already incorporated (as a positive factor) into
global_score during the background scoring job, this module provides an
additional age-sensitive penalty applied inline during the personalised
re-rank pass.  It is deliberately small to avoid double-counting.

Formula:
    staleness_decay = recency_score_complement * STALENESS_WEIGHT
    where recency_score_complement = (1.0 - recency_score)

So:
    fresh content (recency_score ≈ 1.0) → staleness_decay ≈ 0
    stale content (recency_score ≈ 0.0) → staleness_decay ≈ STALENESS_WEIGHT

Relationship to global_score:
    global_score already contains   +recency_weight * recency_score    (≈ +0.20 * 0.70 = +0.14 for fresh content)
    staleness_decay adds            -(1 - recency_score) * STALENESS_WEIGHT (≈ -0.05 * 0.30 = -0.015)
    Net effect: this gently amplifies the recency signal without replacing it.
"""

import os

STALENESS_WEIGHT: float = float(os.getenv("STALENESS_WEIGHT", "0.05"))


def compute_staleness_decay(recency_score: float) -> float:
    """
    Compute the staleness penalty from a pre-computed recency score.

    Args:
        recency_score: Output of compute_recency_score() — value in [0.0, 1.0]
            where 1.0 = brand new, approaching 0.0 = old.

    Returns:
        Additive penalty to subtract from the personalised score (>= 0).
    """
    # Clamp recency_score to valid range
    clamped = max(0.0, min(1.0, recency_score))
    return (1.0 - clamped) * STALENESS_WEIGHT
