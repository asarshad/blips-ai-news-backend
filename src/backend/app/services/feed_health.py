"""
Feed inventory health metrics.

Provides lightweight, in-process computation of category and source
distribution across a promoted feed window.  Used by the
``/metrics/inventory/health`` endpoint to help operators detect:

- Single-source dominance  (any source > 30 % of window)
- Category imbalance       (e.g. AI crowding out Infra/Security)
- Infrastructure coverage  (infra_share_pct target ≥ 10 %)

Designed to accept the same duck-typed items as ``ai_metrics.py``
(plain dicts or ORM models), so it works in both production and tests.
"""

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_primary_topic(item: Any) -> str:
    """Return the first topic tag, or 'unknown'."""
    if isinstance(item, dict):
        topics = item.get("topics") or []
    else:
        topics = getattr(item, "topics", None) or []
    return topics[0] if topics else "unknown"


def _get_source(item: Any) -> str:
    if isinstance(item, dict):
        return item.get("source") or "unknown"
    return getattr(item, "source", None) or "unknown"


def _pct(count: int, total: int) -> float:
    """Return count / total as a 0–100 percentage rounded to 1 dp."""
    return round(count / max(total, 1) * 100, 1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_INFRA_TOPICS = {"Infrastructure", "Cloud", "DevOps", "Security"}


def compute_inventory_health(items: List[Any]) -> Dict[str, Any]:
    """
    Compute category and source distribution for a feed window.

    Args:
        items: List of content items (dicts or ORM models).

    Returns:
        ``category_distribution``
            Raw counts per primary topic.

        ``source_distribution``
            Raw counts per source name.

        ``category_share_pct``
            Percentage share per primary topic (0–100, sum ≈ 100).

        ``source_share_pct``
            Percentage share per source (0–100).

        ``top_source``
            Name of the source with the highest item count.

        ``dominant_source_pct``
            Percentage share of the top source.

        ``infra_share_pct``
            Combined share of infra-adjacent topics (Infrastructure,
            Cloud, DevOps, Security).  Target ≥ 10 %.

        ``total_items``
            Number of items analysed.
    """
    if not items:
        return {
            "category_distribution": {},
            "source_distribution": {},
            "category_share_pct": {},
            "source_share_pct": {},
            "top_source": None,
            "dominant_source_pct": 0.0,
            "infra_share_pct": 0.0,
            "total_items": 0,
        }

    total = len(items)
    cat_counts: Dict[str, int] = {}
    src_counts: Dict[str, int] = {}

    for item in items:
        topic = _get_primary_topic(item)
        source = _get_source(item)
        cat_counts[topic] = cat_counts.get(topic, 0) + 1
        src_counts[source] = src_counts.get(source, 0) + 1

    cat_share = {k: _pct(v, total) for k, v in cat_counts.items()}
    src_share = {k: _pct(v, total) for k, v in src_counts.items()}

    # Dominant source
    top_source: Optional[str] = max(src_counts, key=lambda k: src_counts[k])
    dominant_pct = _pct(src_counts[top_source], total) if top_source else 0.0

    # Infra coverage — sum of infra-adjacent topic shares
    infra_pct = round(
        sum(cat_share.get(t, 0.0) for t in _INFRA_TOPICS),
        1,
    )

    return {
        "category_distribution": dict(sorted(cat_counts.items(), key=lambda x: -x[1])),
        "source_distribution": dict(sorted(src_counts.items(), key=lambda x: -x[1])),
        "category_share_pct": dict(sorted(cat_share.items(), key=lambda x: -x[1])),
        "source_share_pct": dict(sorted(src_share.items(), key=lambda x: -x[1])),
        "top_source": top_source,
        "dominant_source_pct": dominant_pct,
        "infra_share_pct": infra_pct,
        "total_items": total,
    }
