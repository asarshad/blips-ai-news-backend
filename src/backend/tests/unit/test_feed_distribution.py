"""
Unit tests for feed inventory health metrics.

Verifies:
- compute_inventory_health() returns correct category and source counts
- category_share_pct and source_share_pct sum to ≈ 100 %
- top_source and dominant_source_pct are correctly identified
- infra_share_pct aggregates Infrastructure/Cloud/DevOps/Security topics
- single-source dominance detection (> 30 % → warning flag on endpoint)
- infra coverage floor (< 10 % → low flag on endpoint)
- empty-item edge case returns zero-valued result
- works with both plain dicts and attribute-based objects (ORM-style)
"""

from typing import Any, Dict, List

from app.services.feed_health import compute_inventory_health

# ============================================================================
# Helpers
# ============================================================================


def make_item(
    id: int,
    source: str,
    topic: str = "Tech",
) -> Dict[str, Any]:
    """Minimal content-item dict compatible with compute_inventory_health."""
    return {"id": id, "source": source, "topics": [topic]}


def make_items(
    n: int,
    source: str = "TechCrunch",
    topic: str = "Tech",
    start_id: int = 1,
) -> List[Dict[str, Any]]:
    return [make_item(id=start_id + i, source=source, topic=topic) for i in range(n)]


class FakeItem:
    """Attribute-based fake — mimics a SQLAlchemy ORM row."""

    def __init__(self, source: str, topic: str = "Tech"):
        self.source = source
        self.topics = [topic]


# ============================================================================
# Section 1: Empty input
# ============================================================================


def test_empty_items_returns_zero_values():
    result = compute_inventory_health([])
    assert result["total_items"] == 0
    assert result["category_distribution"] == {}
    assert result["source_distribution"] == {}
    assert result["category_share_pct"] == {}
    assert result["source_share_pct"] == {}
    assert result["top_source"] is None
    assert result["dominant_source_pct"] == 0.0
    assert result["infra_share_pct"] == 0.0


# ============================================================================
# Section 2: Category distribution
# ============================================================================


def test_single_category():
    items = make_items(10, topic="AI")
    result = compute_inventory_health(items)
    assert result["category_distribution"] == {"AI": 10}
    assert result["category_share_pct"]["AI"] == 100.0


def test_equal_split_two_categories():
    items = make_items(5, topic="AI") + make_items(5, topic="Tech")
    result = compute_inventory_health(items)
    assert result["category_distribution"]["AI"] == 5
    assert result["category_distribution"]["Tech"] == 5
    assert result["category_share_pct"]["AI"] == 50.0
    assert result["category_share_pct"]["Tech"] == 50.0


def test_category_share_sums_to_100():
    items = (
        make_items(10, topic="AI")
        + make_items(8, topic="Tech")
        + make_items(7, topic="Infrastructure")
        + make_items(5, topic="Security")
    )
    result = compute_inventory_health(items)
    total_share = sum(result["category_share_pct"].values())
    assert abs(total_share - 100.0) < 0.5  # small rounding tolerance


def test_items_without_topics_classified_as_unknown():
    items = [{"id": 1, "source": "X", "topics": []}, {"id": 2, "source": "Y"}]
    result = compute_inventory_health(items)
    assert "unknown" in result["category_distribution"]
    assert result["category_distribution"]["unknown"] == 2


# ============================================================================
# Section 3: Source distribution
# ============================================================================


def test_source_counts():
    items = make_items(3, source="TechCrunch") + make_items(2, source="Wired")
    result = compute_inventory_health(items)
    assert result["source_distribution"]["TechCrunch"] == 3
    assert result["source_distribution"]["Wired"] == 2


def test_top_source_identified():
    items = make_items(5, source="TechCrunch") + make_items(2, source="Wired")
    result = compute_inventory_health(items)
    assert result["top_source"] == "TechCrunch"


def test_dominant_source_pct_calculation():
    # 20 items total; 10 from one source → 50 %
    items = make_items(10, source="Dominant") + make_items(10, source="Other")
    result = compute_inventory_health(items)
    assert result["dominant_source_pct"] == 50.0


def test_source_share_sums_to_100():
    items = (
        make_items(10, source="A")
        + make_items(15, source="B")
        + make_items(5, source="C")
        + make_items(20, source="D")
    )
    result = compute_inventory_health(items)
    total = sum(result["source_share_pct"].values())
    assert abs(total - 100.0) < 0.5


# ============================================================================
# Section 4: Dominant-source detection (30 % threshold)
# ============================================================================


def test_no_dominant_source_warning_when_under_30_pct():
    """
    50 items spread across 4 sources — no single source exceeds 30 %.
    This mirrors the acceptance criterion from the rebalancing plan.
    """
    items = (
        make_items(12, source="TechCrunch")
        + make_items(12, source="Wired")
        + make_items(13, source="Ars Technica")
        + make_items(13, source="The Verge")
    )
    result = compute_inventory_health(items)
    assert result["dominant_source_pct"] <= 30.0, (
        f"top source holds {result['dominant_source_pct']}% (> 30%) in a balanced 50-item window"
    )


def test_dominant_source_over_30_pct():
    """Single source holding 40 % should be detectable."""
    items = make_items(20, source="BigSite") + make_items(30, source="Other")
    result = compute_inventory_health(items)
    # "Other" has 30/50 = 60 % — comfortably above the 30 % warning threshold
    assert result["dominant_source_pct"] == 60.0
    assert result["top_source"] == "Other"


# ============================================================================
# Section 5: Infrastructure coverage
# ============================================================================


def test_infra_share_aggregates_infra_adjacent_topics():
    """
    Infrastructure, Cloud, DevOps, and Security topics should all
    contribute to infra_share_pct.
    """
    items = (
        make_items(5, topic="Infrastructure")
        + make_items(5, topic="Cloud")
        + make_items(5, topic="DevOps")
        + make_items(5, topic="Security")
        + make_items(20, topic="Tech")
    )
    result = compute_inventory_health(items)
    # 20/40 = 50 %
    assert result["infra_share_pct"] == 50.0


def test_infra_share_zero_when_no_infra_topics():
    items = make_items(10, topic="Tech") + make_items(10, topic="AI")
    result = compute_inventory_health(items)
    assert result["infra_share_pct"] == 0.0


def test_infra_coverage_floor_breach_detectable():
    """
    When infra topics represent < 10 % of the window, the endpoint
    should surface an infra_coverage_low alert.  Here we test the raw
    service value that the endpoint reads.
    """
    # 2 infra items out of 50 = 4 %
    items = make_items(2, topic="Infrastructure") + make_items(48, topic="Tech")
    result = compute_inventory_health(items)
    assert result["infra_share_pct"] < 10.0


def test_infra_coverage_above_floor():
    # 6 infra items out of 50 = 12 %
    items = make_items(6, topic="Infrastructure") + make_items(44, topic="Tech")
    result = compute_inventory_health(items)
    assert result["infra_share_pct"] >= 10.0


# ============================================================================
# Section 6: ORM-style objects (attribute access)
# ============================================================================


def test_works_with_orm_style_objects():
    items = [
        FakeItem(source="Cloudflare Blog", topic="Infrastructure"),
        FakeItem(source="CNCF Blog", topic="Infrastructure"),
        FakeItem(source="TechCrunch", topic="Tech"),
    ]
    result = compute_inventory_health(items)
    assert result["total_items"] == 3
    assert result["category_distribution"]["Infrastructure"] == 2
    assert result["source_distribution"]["Cloudflare Blog"] == 1


# ============================================================================
# Section 7: total_items
# ============================================================================


def test_total_items_count():
    items = make_items(17)
    result = compute_inventory_health(items)
    assert result["total_items"] == 17


# ============================================================================
# Section 8: Sorted output (highest count first)
# ============================================================================


def test_category_distribution_sorted_descending():
    items = (
        make_items(3, topic="Tech") + make_items(7, topic="AI") + make_items(1, topic="Security")
    )
    result = compute_inventory_health(items)
    counts = list(result["category_distribution"].values())
    assert counts == sorted(counts, reverse=True)


def test_source_distribution_sorted_descending():
    items = make_items(5, source="A") + make_items(10, source="B") + make_items(2, source="C")
    result = compute_inventory_health(items)
    counts = list(result["source_distribution"].values())
    assert counts == sorted(counts, reverse=True)
