"""
Personalised feed score computation.

Augments the stored global_score with per-request signals that depend on
who is viewing the feed.  This score is computed in-memory at serve time;
the underlying global_score (quality + trend + recency + diversity) is
still written to PostgreSQL by the background scoring job.

Formula
-------
    final_score =
        base_score                  (= global_score from DB)
      + engagement_score            (= trend_score, already folded into base)
      + interest_boost              (declared-category match, decays with engagement)
      - staleness_decay             (supplemental age penalty)
      - source_dominance_penalty    (prevents any source dominating the window)

Where:
    engagement_score is NOT added separately — it is already incorporated in
    global_score via the trend component.  The formula above is the conceptual
    schema; computationally we start from global_score and apply the three
    personalisation adjustments.

Usage
-----
    from app.ranking.feed_score import compute_feed_score, rerank_feed

    # Re-rank a list of ContentItem (or dicts) for a given user context
    ranked = rerank_feed(
        items=tiered_items,
        selected_categories=["AI", "Security"],
        engagement_decay_factor=0.2,
    )
"""

from typing import Any, Dict, List, Optional

from app.ranking.dominance import compute_source_dominance_penalty
from app.ranking.interest import compute_engagement_decay_factor, compute_interest_boost
from app.ranking.staleness import compute_staleness_decay


def compute_feed_score(
    global_score: float,
    item_topics: List[str],
    recency_score: float,
    source: str,
    source_counts: Dict[str, int],
    selected_categories: Optional[List[str]] = None,
    engagement_decay_factor: float = 0.0,
    window_size: int = 20,
) -> float:
    """
    Compute the personalised feed score for a single content item.

    Args:
        global_score: Pre-computed base score from the DB scoring job.
        item_topics: Extracted topics for the item (first = primary topic).
        recency_score: Current recency score (0–1, 1 = fresh).
        source: Content source name.
        source_counts: Mapping of source → count in the current re-rank window,
            including all items ranked BEFORE this one.
        selected_categories: User's declared category interests.
        engagement_decay_factor: 0 = full interest boost; 1 = no boost.
        window_size: Window used to normalise source dominance.

    Returns:
        Personalised score (may exceed 1.0 for high-interest fresh content).
    """
    boost = compute_interest_boost(
        item_topics=item_topics,
        selected_categories=selected_categories or [],
        engagement_decay_factor=engagement_decay_factor,
    )
    staleness = compute_staleness_decay(recency_score)
    dominance = compute_source_dominance_penalty(
        source=source,
        source_counts=source_counts,
        window_size=window_size,
    )

    return global_score + boost - staleness - dominance


def explain_feed_score(
    global_score: float,
    item_topics: List[str],
    recency_score: float,
    source: str,
    source_counts: Dict[str, int],
    selected_categories: Optional[List[str]] = None,
    engagement_decay_factor: float = 0.0,
    window_size: int = 20,
) -> Dict[str, Any]:
    """
    Return a human-readable breakdown of the personalised score.

    Useful for feed debugging endpoints and unit tests.
    """
    from app.ranking.dominance import DOMINANCE_WEIGHT, MAX_SOURCE_PCT
    from app.ranking.interest import INTEREST_WEIGHT
    from app.ranking.staleness import STALENESS_WEIGHT

    boost = compute_interest_boost(
        item_topics=item_topics,
        selected_categories=selected_categories or [],
        engagement_decay_factor=engagement_decay_factor,
    )
    staleness = compute_staleness_decay(recency_score)
    dominance = compute_source_dominance_penalty(
        source=source,
        source_counts=source_counts,
        window_size=window_size,
    )
    final = global_score + boost - staleness - dominance

    return {
        "final_score": round(final, 4),
        "global_score": round(global_score, 4),
        "interest_boost": round(boost, 4),
        "staleness_decay": round(staleness, 4),
        "source_dominance_penalty": round(dominance, 4),
        "primary_topic": item_topics[0] if item_topics else None,
        "category_matched": bool(
            item_topics and selected_categories and item_topics[0] in selected_categories
        ),
        "engagement_decay_factor": round(engagement_decay_factor, 4),
        "config": {
            "INTEREST_WEIGHT": INTEREST_WEIGHT,
            "STALENESS_WEIGHT": STALENESS_WEIGHT,
            "DOMINANCE_WEIGHT": DOMINANCE_WEIGHT,
            "MAX_SOURCE_PCT": MAX_SOURCE_PCT,
        },
    }


def rerank_feed(
    items: List[Any],
    selected_categories: Optional[List[str]] = None,
    total_learned_weight: float = 0.0,
    window_size: int = 20,
) -> List[Any]:
    """
    Re-rank a list of content items using the personalised feed score.

    Items are expected to have:
        - .global_score (float)
        - .topics (list[str])
        - .recency_score (float)
        - .source (str)
    or be plain dicts with equivalent keys.

    Non-selected categories are NEVER filtered — every item remains in the
    result; only the ordering changes.

    Args:
        items: Unordered or pre-sorted content items.
        selected_categories: User's declared category interests.  Pass None
            or [] for an unauthenticated / preference-free user.
        total_learned_weight: Sum of all UserPreference.weight values for
            the user — used to derive engagement_decay_factor.
        window_size: Rolling window size for source dominance calculation.

    Returns:
        New list sorted by personalised feed score, descending.
    """
    if not items:
        return []

    decay = compute_engagement_decay_factor(total_learned_weight)

    # Running tally for source dominance (built as we process items in order)
    source_counts: Dict[str, int] = {}
    scored: List[tuple] = []

    for item in items:
        # Support both ORM model attributes and dict keys
        global_score = (
            item.global_score if hasattr(item, "global_score") else item.get("global_score", 0.0)
        )
        topics = item.topics if hasattr(item, "topics") else item.get("topics", [])
        recency = (
            item.recency_score if hasattr(item, "recency_score") else item.get("recency_score", 1.0)
        )
        source = item.source if hasattr(item, "source") else item.get("source", "")

        # Increment source count BEFORE computing penalty so the current item's
        # contribution is reflected.  This mirrors the diversity_mixer approach
        # of checking "would adding this item violate the constraint?".
        source_counts[source] = source_counts.get(source, 0) + 1

        score = compute_feed_score(
            global_score=global_score or 0.0,
            item_topics=topics or [],
            recency_score=recency or 1.0,
            source=source,
            source_counts=source_counts,
            selected_categories=selected_categories,
            engagement_decay_factor=decay,
            window_size=window_size,
        )
        scored.append((score, item))

    # Sort descending by personalised score
    scored.sort(key=lambda t: t[0], reverse=True)
    return [item for _, item in scored]
