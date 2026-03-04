"""
AI coverage metrics for feed-quality monitoring.

Provides lightweight, in-process computation of three key metrics that
measure how much AI content is present in a rendered feed window:

ai_share_pct
    Fraction (0–100) of items whose primary topic is "AI".
    Target: ≤ 40 % (enforced upstream by DiversityMixer).

ai_cluster_hotness
    Average ``signal_hits`` across AI-category items.  ``signal_hits`` is
    the number of distinct external signals (HackerNews, GitHub trending,
    YT trending) that referenced the same story — a proxy for "how hot is
    this AI cluster right now".

ai_source_diversity
    Number of distinct source names among AI-category items.  A higher
    value means coverage is spread across more publishers, rather than
    being dominated by a single outlet.

These are computed on plain Python dicts or any object with ``topics``,
``source``, and optionally ``signal_hits`` attributes, so they work with
both ORM models and the lightweight dicts used in unit tests.
"""

from typing import Any, Dict, List, Optional

_AI_TOPIC = "AI"


def _get_primary_topic(item: Any) -> Optional[str]:
    """Extract the primary topic (first element of topics list)."""
    if isinstance(item, dict):
        topics = item.get("topics") or []
    else:
        topics = getattr(item, "topics", None) or []
    return topics[0] if topics else None


def _get_source(item: Any) -> str:
    if isinstance(item, dict):
        return item.get("source", "unknown")
    return getattr(item, "source", "unknown")


def _get_signal_hits(item: Any) -> int:
    if isinstance(item, dict):
        return item.get("signal_hits", 0) or 0
    return getattr(item, "signal_hits", 0) or 0


def compute_ai_feed_metrics(items: List[Any]) -> Dict[str, Any]:
    """
    Compute AI coverage metrics for a feed window.

    Args:
        items: List of content items (dicts or ORM models).  The list
               represents one logical feed window (e.g. the 20 items
               returned to the client in one request).

    Returns:
        A dict with the following keys:

        ``ai_share_pct`` (float)
            Percentage of items that are AI-category (0.0–100.0).

        ``ai_cluster_hotness`` (float)
            Mean ``signal_hits`` for AI items.  0.0 if no AI items.

        ``ai_source_diversity`` (int)
            Number of distinct source names among AI items.
    """
    if not items:
        return {
            "ai_share_pct": 0.0,
            "ai_cluster_hotness": 0.0,
            "ai_source_diversity": 0,
        }

    ai_items = [it for it in items if _get_primary_topic(it) == _AI_TOPIC]
    total = len(items)

    ai_share_pct = round(len(ai_items) / total * 100, 1) if total else 0.0

    if ai_items:
        total_hits = sum(_get_signal_hits(it) for it in ai_items)
        ai_cluster_hotness = round(total_hits / len(ai_items), 2)
        ai_source_diversity = len({_get_source(it) for it in ai_items})
    else:
        ai_cluster_hotness = 0.0
        ai_source_diversity = 0

    return {
        "ai_share_pct": ai_share_pct,
        "ai_cluster_hotness": ai_cluster_hotness,
        "ai_source_diversity": ai_source_diversity,
    }


def ai_share_exceeds_cap(items: List[Any], cap_pct: float = 40.0) -> bool:
    """
    Return True if the AI share of *items* exceeds *cap_pct*.

    Convenience wrapper used in monitoring and alert logic.

    Args:
        items: Feed window items.
        cap_pct: Cap threshold in percent (default 40.0).
    """
    metrics = compute_ai_feed_metrics(items)
    return metrics["ai_share_pct"] > cap_pct
