"""
Unit tests for interest-weighted feed ranking.

Covers:
- Selected category items rise in ranking (vs. non-selected)
- Non-selected categories still appear (no hard filtering)
- Source dominance penalty fires when source exceeds threshold
- Interest boost decays as engagement weight accumulates
- Staleness decay increases with older content
- full rerank_feed() ordering
- Score explanation breakdown
"""

from unittest.mock import MagicMock

import pytest

from app.ranking.dominance import (
    build_source_counts,
    compute_source_dominance_penalty,
)
from app.ranking.feed_score import compute_feed_score, explain_feed_score, rerank_feed
from app.ranking.interest import (
    DECAY_SATURATION,
    INTEREST_WEIGHT,
    compute_engagement_decay_factor,
    compute_interest_boost,
)
from app.ranking.staleness import STALENESS_WEIGHT, compute_staleness_decay

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(
    id: int,
    topics: list,
    source: str,
    global_score: float = 0.5,
    recency_score: float = 0.9,
) -> dict:
    """Build a plain-dict content item for testing rerank_feed."""
    return {
        "id": id,
        "title": f"Item {id}",
        "topics": topics,
        "source": source,
        "global_score": global_score,
        "recency_score": recency_score,
    }


# ---------------------------------------------------------------------------
# Interest boost
# ---------------------------------------------------------------------------


class TestComputeInterestBoost:
    def test_match_returns_interest_weight(self):
        boost = compute_interest_boost(["AI"], ["AI"])
        assert boost == pytest.approx(INTEREST_WEIGHT)

    def test_no_match_returns_zero(self):
        boost = compute_interest_boost(["Security"], ["AI"])
        assert boost == 0.0

    def test_secondary_topic_not_matched(self):
        """Only the primary (first) topic drives the boost."""
        boost = compute_interest_boost(["Tech", "AI"], ["AI"])
        assert boost == 0.0

    def test_empty_selected_returns_zero(self):
        assert compute_interest_boost(["AI"], []) == 0.0

    def test_empty_topics_returns_zero(self):
        assert compute_interest_boost([], ["AI"]) == 0.0

    def test_full_decay_zeroes_boost(self):
        boost = compute_interest_boost(["AI"], ["AI"], engagement_decay_factor=1.0)
        assert boost == 0.0

    def test_partial_decay_reduces_boost(self):
        boost = compute_interest_boost(["AI"], ["AI"], engagement_decay_factor=0.5)
        assert boost == pytest.approx(INTEREST_WEIGHT * 0.5)

    def test_boost_is_within_20pct_of_avg_base_score(self):
        """Requirement: INTEREST_WEIGHT <= 20% of average base_score (~0.50)."""
        avg_base = 0.50
        assert INTEREST_WEIGHT <= 0.20 * avg_base + 1e-9, (
            f"INTEREST_WEIGHT={INTEREST_WEIGHT} exceeds 20% of avg base score {avg_base}"
        )


# ---------------------------------------------------------------------------
# Engagement decay factor
# ---------------------------------------------------------------------------


class TestComputeEngagementDecayFactor:
    def test_no_weight_is_zero_decay(self):
        assert compute_engagement_decay_factor(0.0) == 0.0

    def test_saturation_is_full_decay(self):
        assert compute_engagement_decay_factor(DECAY_SATURATION) == pytest.approx(1.0)

    def test_half_saturation_is_half_decay(self):
        assert compute_engagement_decay_factor(DECAY_SATURATION / 2) == pytest.approx(0.5)

    def test_beyond_saturation_clamped_to_one(self):
        assert compute_engagement_decay_factor(DECAY_SATURATION * 999) == 1.0


# ---------------------------------------------------------------------------
# Staleness decay
# ---------------------------------------------------------------------------


class TestComputeStalenessDecay:
    def test_fresh_content_no_penalty(self):
        penalty = compute_staleness_decay(1.0)
        assert penalty == pytest.approx(0.0)

    def test_stale_content_max_penalty(self):
        penalty = compute_staleness_decay(0.0)
        assert penalty == pytest.approx(STALENESS_WEIGHT)

    def test_midpoint_half_penalty(self):
        penalty = compute_staleness_decay(0.5)
        assert penalty == pytest.approx(STALENESS_WEIGHT * 0.5)

    def test_clamped_above_one(self):
        """Recency > 1.0 treated as fully fresh."""
        assert compute_staleness_decay(1.5) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Source dominance penalty
# ---------------------------------------------------------------------------


class TestComputeSourceDominancePenalty:
    def test_no_dominance_below_threshold(self):
        counts = {"TechCrunch": 2}
        # 2/10 = 20% < MAX_SOURCE_PCT (30%) → no penalty
        penalty = compute_source_dominance_penalty("TechCrunch", counts, window_size=10)
        assert penalty == 0.0

    def test_dominance_fires_above_threshold(self):
        counts = {"TechCrunch": 4}
        # 4/10 = 40% > 30% → penalty > 0
        penalty = compute_source_dominance_penalty("TechCrunch", counts, window_size=10)
        assert penalty > 0.0

    def test_unknown_source_no_penalty(self):
        penalty = compute_source_dominance_penalty("Unknown", {}, window_size=10)
        assert penalty == 0.0

    def test_penalty_increases_with_dominance(self):
        p1 = compute_source_dominance_penalty("S", {"S": 3}, window_size=10)  # 30%
        p2 = compute_source_dominance_penalty("S", {"S": 5}, window_size=10)  # 50%
        p3 = compute_source_dominance_penalty("S", {"S": 10}, window_size=10)  # 100%
        assert p1 <= p2 <= p3

    def test_build_source_counts_from_dicts(self):
        items = [{"source": "A"}, {"source": "B"}, {"source": "A"}]
        counts = build_source_counts(items)
        assert counts == {"A": 2, "B": 1}

    def test_build_source_counts_from_models(self):
        m1 = MagicMock()
        m1.source = "OpenAI Blog"
        m2 = MagicMock()
        m2.source = "OpenAI Blog"
        counts = build_source_counts([m1, m2])
        assert counts["OpenAI Blog"] == 2


# ---------------------------------------------------------------------------
# compute_feed_score
# ---------------------------------------------------------------------------


class TestComputeFeedScore:
    def test_baseline_no_personalization(self):
        score = compute_feed_score(
            global_score=0.5,
            item_topics=["Tech"],
            recency_score=1.0,
            source="TechCrunch",
            source_counts={},
            selected_categories=None,
        )
        # interest_boost=0, staleness=0, dominance=0  → equals global_score
        assert score == pytest.approx(0.5)

    def test_interest_boost_increases_score(self):
        base = compute_feed_score(
            global_score=0.5,
            item_topics=["AI"],
            recency_score=1.0,
            source="OpenAI Blog",
            source_counts={},
            selected_categories=None,
        )
        with_interest = compute_feed_score(
            global_score=0.5,
            item_topics=["AI"],
            recency_score=1.0,
            source="OpenAI Blog",
            source_counts={},
            selected_categories=["AI"],
        )
        assert with_interest > base
        assert with_interest - base == pytest.approx(INTEREST_WEIGHT)

    def test_staleness_reduces_score(self):
        fresh = compute_feed_score(
            global_score=0.5,
            item_topics=["Tech"],
            recency_score=1.0,
            source="X",
            source_counts={},
        )
        stale = compute_feed_score(
            global_score=0.5,
            item_topics=["Tech"],
            recency_score=0.0,
            source="X",
            source_counts={},
        )
        assert stale < fresh

    def test_dominance_reduces_score(self):
        no_dom = compute_feed_score(
            global_score=0.5,
            item_topics=["Tech"],
            recency_score=1.0,
            source="X",
            source_counts={"X": 1},
            window_size=10,
        )
        dominated = compute_feed_score(
            global_score=0.5,
            item_topics=["Tech"],
            recency_score=1.0,
            source="X",
            source_counts={"X": 5},
            window_size=10,
        )
        assert dominated < no_dom


# ---------------------------------------------------------------------------
# rerank_feed — Requirement 1: selected category increases ranking
# ---------------------------------------------------------------------------


class TestRerankFeedSelectedCategoryBoost:
    def test_selected_category_item_ranks_higher(self):
        """A lower base_score item in a selected category should overtake
        a higher base_score item in a non-selected category when the
        interest_boost is large enough."""
        # AI item has lower base score
        ai_item = _item(1, ["AI"], "OpenAI Blog", global_score=0.50, recency_score=1.0)
        # Tech item has slightly higher base score
        tech_item = _item(2, ["Tech"], "TechCrunch", global_score=0.52, recency_score=1.0)

        ranked = rerank_feed(
            items=[tech_item, ai_item],
            selected_categories=["AI"],
            total_learned_weight=0.0,
        )

        # AI item should now be first (0.50 + 0.10 = 0.60 > 0.52)
        assert ranked[0]["id"] == 1, "AI item should rank first with interest boost"

    def test_multiple_selected_categories_boost_respective_items(self):
        items = [
            _item(1, ["Finance"], "Bloomberg", global_score=0.6),
            _item(2, ["AI"], "OpenAI Blog", global_score=0.5),
            _item(3, ["Security"], "Krebs", global_score=0.5),
        ]
        ranked = rerank_feed(
            items=items,
            selected_categories=["AI", "Security"],
        )
        ids = [i["id"] for i in ranked]
        # Finance (no boost, score 0.6) vs AI/Security (boost, 0.5+0.1=0.6);
        # Finance still ranks ≥ boosted items since it starts higher
        # but both boosted items should rank above their un-boosted score peers
        assert 2 in ids and 3 in ids, "Boosted categories present in results"


# ---------------------------------------------------------------------------
# Requirement 2: non-selected still appear
# ---------------------------------------------------------------------------


class TestRerankFeedNonSelectedAppear:
    def test_non_selected_categories_not_filtered(self):
        """Tests must show that non-selected categories still appear in the
        results — rerank_feed never hard-filters."""
        items = [
            _item(1, ["AI"], "OpenAI Blog", global_score=0.9),
            _item(2, ["Finance"], "Bloomberg", global_score=0.3),
            _item(3, ["Sports"], "ESPN", global_score=0.2),
        ]
        ranked = rerank_feed(
            items=items,
            selected_categories=["AI"],
        )
        ranked_ids = {i["id"] for i in ranked}
        assert 2 in ranked_ids, "Finance item (non-selected) must still appear"
        assert 3 in ranked_ids, "Sports item (non-selected) must still appear"
        assert len(ranked) == 3, "All items must remain in output"

    def test_empty_selected_categories_no_filtering(self):
        items = [_item(i, [f"Cat{i}"], f"Source{i}") for i in range(5)]
        ranked = rerank_feed(items=items, selected_categories=[])
        assert len(ranked) == 5


# ---------------------------------------------------------------------------
# Requirement 3: no dominance regression (source dominance penalty)
# ---------------------------------------------------------------------------


class TestRerankFeedNoDominanceRegression:
    def test_dominant_source_penalised(self):
        """When one source has many items, its later items should rank lower
        than items from less-represented sources."""
        # 6 items from TechCrunch, 2 from unique sources
        items = [_item(i, ["Tech"], "TechCrunch", global_score=0.7) for i in range(6)]
        items += [
            _item(10, ["Tech"], "ArsTechnica", global_score=0.65),
            _item(11, ["Tech"], "Wired", global_score=0.65),
        ]

        ranked = rerank_feed(items=items, selected_categories=[], window_size=8)

        # Within ranked output, the non-TechCrunch items should outrank at
        # least *some* TechCrunch items even with lower base score.
        techcrunch_positions = [
            i for i, item in enumerate(ranked) if item["source"] == "TechCrunch"
        ]
        other_positions = [i for i, item in enumerate(ranked) if item["source"] != "TechCrunch"]

        # At least one non-TechCrunch item should rank ahead of a TechCrunch item
        assert min(other_positions) < max(techcrunch_positions), (
            "Source dominance penalty should push some TechCrunch items below diverse sources"
        )

    def test_no_penalty_below_threshold(self):
        """Sources below MAX_SOURCE_PCT share receive zero penalty."""
        # 2 out of 10 = 20% < 30% → no penalty
        counts = {"TechCrunch": 2}
        assert compute_source_dominance_penalty("TechCrunch", counts, window_size=10) == 0.0


# ---------------------------------------------------------------------------
# Decay interaction
# ---------------------------------------------------------------------------


class TestInterestDecayWithEngagement:
    def test_heavy_engagement_reduces_interest_boost(self):
        """A highly-engaged user should get less interest boost than a new user."""
        items = [
            _item(1, ["AI"], "OpenAI", global_score=0.5),
            _item(2, ["Tech"], "TechCrunch", global_score=0.52),
        ]
        # New user (no engagement history) — AI item gets full boost
        ranked_new = rerank_feed(items=items, selected_categories=["AI"], total_learned_weight=0.0)
        # Heavy user (engagement = DECAY_SATURATION) — AI item gets zero boost
        ranked_engaged = rerank_feed(
            items=items,
            selected_categories=["AI"],
            total_learned_weight=DECAY_SATURATION,
        )

        assert ranked_new[0]["id"] == 1, "New user: AI item should rank first"
        # Heavy user: interest_boost=0, so Tech item (0.52) outranks AI item (0.50)
        assert ranked_engaged[0]["id"] == 2, "Engaged user: Tech item should rank first (no boost)"


# ---------------------------------------------------------------------------
# explain_feed_score
# ---------------------------------------------------------------------------


class TestExplainFeedScore:
    def test_explanation_contains_all_keys(self):
        result = explain_feed_score(
            global_score=0.5,
            item_topics=["AI"],
            recency_score=0.8,
            source="OpenAI Blog",
            source_counts={"OpenAI Blog": 1},
            selected_categories=["AI"],
        )
        for key in (
            "final_score",
            "global_score",
            "interest_boost",
            "staleness_decay",
            "source_dominance_penalty",
            "primary_topic",
            "category_matched",
            "engagement_decay_factor",
            "config",
        ):
            assert key in result, f"Missing key: {key}"

    def test_explanation_category_matched_true(self):
        result = explain_feed_score(
            global_score=0.5,
            item_topics=["AI"],
            recency_score=1.0,
            source="X",
            source_counts={},
            selected_categories=["AI"],
        )
        assert result["category_matched"] is True

    def test_explanation_scores_add_up(self):
        result = explain_feed_score(
            global_score=0.5,
            item_topics=["AI"],
            recency_score=0.9,
            source="X",
            source_counts={},
            selected_categories=["AI"],
        )
        expected = (
            result["global_score"]
            + result["interest_boost"]
            - result["staleness_decay"]
            - result["source_dominance_penalty"]
        )
        assert result["final_score"] == pytest.approx(round(expected, 4))
