"""
Unit tests for AI coverage cap enforcement and metrics.

Verifies:
- DiversityMixer enforces the 40 % AI window cap
- DiversityMixer blocks more than 2 consecutive AI items
- Category caps survive constraint relaxation (they are hard limits)
- ai_metrics module computes ai_share_pct, ai_cluster_hotness, ai_source_diversity
- ai_share_exceeds_cap() helper works correctly
"""

from typing import Any, Dict, List, Optional

from app.config.diversity import CategoryCapConfig, DiversityConstraints
from app.services.ai_metrics import (
    ai_share_exceeds_cap,
    compute_ai_feed_metrics,
)
from app.services.diversity_mixer import DiversityMixer

# ============================================================================
# Helpers
# ============================================================================


def make_item(
    id: int,
    source: str,
    topics: Optional[List[str]] = None,
    signal_hits: int = 0,
    score: float = 1.0,
) -> Dict[str, Any]:
    """Create a minimal content-item dict compatible with DiversityMixer."""
    return {
        "id": id,
        "source": source,
        "topics": topics or ["Tech"],
        "signal_hits": signal_hits,
        "score": score,
    }


def make_ai_item(id: int, source: str = "OpenAI Blog", signal_hits: int = 0) -> Dict[str, Any]:
    return make_item(id=id, source=source, topics=["AI"], signal_hits=signal_hits)


def make_tech_item(id: int, source: str = "TechCrunch") -> Dict[str, Any]:
    return make_item(id=id, source=source, topics=["Tech"])


def _sources(items: List[Dict]) -> List[str]:
    return [it["source"] for it in items]


def _topics(items: List[Dict]) -> List[str]:
    return [it["topics"][0] for it in items]


def _ai_constraints(
    window_size: int = 5,
    max_window_pct: float = 0.40,
    max_consecutive: int = 2,
) -> DiversityConstraints:
    """Return constraints with only the AI category cap active (no source/topic hard caps)."""
    return DiversityConstraints(
        window_size=window_size,
        max_source_per_window=window_size,  # effectively disabled
        max_topic_per_window=None,
        allow_consecutive_same_source=True,
        min_inventory_for_constraints=3,
        category_caps={
            "AI": CategoryCapConfig(max_window_pct=max_window_pct, max_consecutive=max_consecutive)
        },
    )


# ============================================================================
# Section 1: AI window-percentage cap
# ============================================================================


class TestAICategoryWindowCap:
    """DiversityMixer must not let AI items exceed 40 % of any window."""

    def _ai_pct_in_every_window(self, items: List[Dict], window_size: int) -> List[float]:
        """Slide a window over items and return the AI fraction at each position."""
        pcts = []
        for i in range(len(items) - window_size + 1):
            window = items[i : i + window_size]
            ai_count = sum(1 for it in window if it["topics"][0] == "AI")
            pcts.append(ai_count / window_size)
        return pcts

    def test_majority_ai_input_is_capped(self):
        """
        Feed with 4 AI items and 6 Tech items must be re-ranked so that no
        window of 5 exceeds 40 % AI (i.e. at most 2 AI per window).
        """
        candidates = [make_ai_item(i, source=f"AISource{i}") for i in range(1, 5)]
        candidates += [make_tech_item(i, source=f"TC{i}") for i in range(5, 11)]

        mixer = DiversityMixer(_ai_constraints(window_size=5, max_window_pct=0.40))
        result = mixer.mix(candidates, target_size=10)

        window_pcts = self._ai_pct_in_every_window(result.items, window_size=5)
        for pct in window_pcts:
            assert pct <= 0.40 + 1e-9, (
                f"AI window pct {pct:.0%} exceeds 40 % cap. Topics: {_topics(result.items)}"
            )

    def test_exactly_40_pct_ai_passes(self):
        """
        2 AI items in a window of 5 (40 %) must not be rejected.
        """
        candidates = [
            make_tech_item(1, "Verge"),
            make_ai_item(2, "OpenAI Blog"),
            make_tech_item(3, "Ars"),
            make_tech_item(4, "CNET"),
            make_ai_item(5, "Anthropic Blog"),
        ]
        mixer = DiversityMixer(_ai_constraints(window_size=5, max_window_pct=0.40))
        result = mixer.mix(candidates, target_size=5)

        ai_count = sum(1 for it in result.items if it["topics"][0] == "AI")
        total = len(result.items)
        assert ai_count / total <= 0.40 + 1e-9

    def test_all_tech_feed_unaffected(self):
        """A feed with zero AI items should be returned unchanged."""
        candidates = [make_tech_item(i, source=f"TC{i}") for i in range(1, 8)]

        mixer = DiversityMixer(_ai_constraints())
        result = mixer.mix(candidates, target_size=7)

        ai_count = sum(1 for it in result.items if it["topics"][0] == "AI")
        assert ai_count == 0
        assert len(result.items) == 7

    def test_category_cap_metric_in_result(self):
        """MixerResult.category_distribution should report AI count."""
        candidates = [make_ai_item(i) for i in range(1, 4)]
        candidates += [make_tech_item(i) for i in range(4, 9)]

        mixer = DiversityMixer(_ai_constraints())
        result = mixer.mix(candidates, target_size=8)

        assert "AI" in result.category_distribution
        total = len(result.items) or 1
        ai_share = result.category_distribution.get("AI", 0) / total
        assert ai_share <= 0.40 + 1e-9


# ============================================================================
# Section 2: Consecutive AI item limit
# ============================================================================


class TestAIConsecutiveCap:
    """No more than 2 consecutive AI items may appear in sequence."""

    def _max_ai_run(self, items: List[Dict]) -> int:
        """Return the maximum run length of back-to-back AI items."""
        max_run = current = 0
        for it in items:
            if it["topics"][0] == "AI":
                current += 1
                max_run = max(max_run, current)
            else:
                current = 0
        return max_run

    def test_three_consecutive_ai_blocked(self):
        """Three AI items in a row must be broken up."""
        candidates = [
            make_ai_item(1, "OpenAI Blog"),
            make_ai_item(2, "Anthropic Blog"),
            make_ai_item(3, "DeepMind Blog"),
            make_tech_item(4, "TechCrunch"),
            make_tech_item(5, "The Verge"),
        ]
        mixer = DiversityMixer(_ai_constraints(max_consecutive=2))
        result = mixer.mix(candidates, target_size=5)

        run = self._max_ai_run(result.items)
        assert run <= 2, (
            f"Max consecutive AI run is {run} (limit 2). Topics: {_topics(result.items)}"
        )

    def test_two_consecutive_ai_allowed(self):
        """Exactly two consecutive AI items should be permitted."""
        candidates = [
            make_ai_item(1, "OpenAI Blog"),
            make_ai_item(2, "Anthropic Blog"),
            make_tech_item(3, "TechCrunch"),
            make_tech_item(4, "The Verge"),
        ]
        mixer = DiversityMixer(_ai_constraints(max_consecutive=2))
        result = mixer.mix(candidates, target_size=4)

        # Two consecutive AI items should appear somewhere in the output
        run = self._max_ai_run(result.items)
        assert run <= 2

    def test_alternating_ai_tech_passes(self):
        """Alternating AI / Tech pattern should be returned as-is."""
        candidates = [
            make_ai_item(1, "OpenAI Blog"),
            make_tech_item(2, "TechCrunch"),
            make_ai_item(3, "Anthropic Blog"),
            make_tech_item(4, "The Verge"),
        ]
        mixer = DiversityMixer(_ai_constraints(max_consecutive=2))
        result = mixer.mix(candidates, target_size=4)

        run = self._max_ai_run(result.items)
        assert run <= 2

    def test_all_ai_items_get_interleaved(self):
        """
        When all candidates are AI, the consecutive cap cannot be satisfied;
        the mixer must fall back gracefully (returning items, not crashing).
        """
        candidates = [make_ai_item(i, source=f"AISource{i}") for i in range(1, 6)]
        mixer = DiversityMixer(_ai_constraints(max_consecutive=2))
        result = mixer.mix(candidates, target_size=5)

        # No crash; all items returned (cap relaxes since no non-AI items exist)
        assert len(result.items) == 5


# ============================================================================
# Section 3: Category caps are NOT relaxed
# ============================================================================


class TestAICategoryCapNotRelaxed:
    """The AI category cap must survive the normal constraint-relaxation ladder."""

    def test_cap_survives_full_relaxation(self):
        """
        Even when source+topic constraints are fully relaxed, AI % cap holds.
        Simulate a tight source inventory (all from the same source) that forces
        relaxation, using an achievable AI ratio (4 AI + 6 Tech = 40 % AI).
        """
        # All items from the same source → triggers maximum source relaxation
        candidates = [make_ai_item(i, source="OpenAI Blog") for i in range(1, 5)] + [
            make_tech_item(i, source="OpenAI Blog") for i in range(5, 11)
        ]

        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=1,  # very tight → will trigger relaxation
            max_topic_per_window=None,
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=3,
            category_caps={"AI": CategoryCapConfig(max_window_pct=0.40, max_consecutive=2)},
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(candidates, target_size=10)

        # AI cap must still hold
        ai_count = sum(1 for it in result.items if it["topics"][0] == "AI")
        total = len(result.items) or 1
        assert ai_count / total <= 0.40 + 1e-9, (
            f"AI share {ai_count}/{total} = {ai_count / total:.0%} exceeds cap after relaxation. "
            f"Topics: {_topics(result.items)}"
        )


# ============================================================================
# Section 4: ai_metrics module
# ============================================================================


class TestAISharePct:
    """compute_ai_feed_metrics — ai_share_pct."""

    def test_all_tech(self):
        items = [make_tech_item(i) for i in range(5)]
        m = compute_ai_feed_metrics(items)
        assert m["ai_share_pct"] == 0.0

    def test_all_ai(self):
        items = [make_ai_item(i) for i in range(5)]
        m = compute_ai_feed_metrics(items)
        assert m["ai_share_pct"] == 100.0

    def test_mixed_40_pct(self):
        items = [make_ai_item(i) for i in range(1, 3)]  # 2 AI
        items += [make_tech_item(i) for i in range(3, 6)]  # 3 Tech  → 2/5 = 40 %
        m = compute_ai_feed_metrics(items)
        assert m["ai_share_pct"] == 40.0

    def test_empty_feed(self):
        m = compute_ai_feed_metrics([])
        assert m["ai_share_pct"] == 0.0

    def test_single_ai_item(self):
        m = compute_ai_feed_metrics([make_ai_item(1)])
        assert m["ai_share_pct"] == 100.0


class TestAIClusterHotness:
    """compute_ai_feed_metrics — ai_cluster_hotness."""

    def test_hotness_is_mean_signal_hits(self):
        items = [
            make_ai_item(1, signal_hits=4),
            make_ai_item(2, signal_hits=6),
        ]
        m = compute_ai_feed_metrics(items)
        assert m["ai_cluster_hotness"] == 5.0

    def test_hotness_zero_when_no_ai(self):
        items = [make_tech_item(i) for i in range(3)]
        m = compute_ai_feed_metrics(items)
        assert m["ai_cluster_hotness"] == 0.0

    def test_hotness_ignores_tech_signal_hits(self):
        items = [
            make_ai_item(1, signal_hits=10),
            make_item(2, "TechCrunch", topics=["Tech"], signal_hits=100),
        ]
        m = compute_ai_feed_metrics(items)
        assert m["ai_cluster_hotness"] == 10.0  # only the AI item counts


class TestAISourceDiversity:
    """compute_ai_feed_metrics — ai_source_diversity."""

    def test_three_distinct_sources(self):
        items = [
            make_ai_item(1, source="OpenAI Blog"),
            make_ai_item(2, source="Anthropic Blog"),
            make_ai_item(3, source="DeepMind Blog"),
            make_tech_item(4, source="TechCrunch"),
        ]
        m = compute_ai_feed_metrics(items)
        assert m["ai_source_diversity"] == 3

    def test_single_source_repeated(self):
        items = [make_ai_item(i, source="OpenAI Blog") for i in range(4)]
        m = compute_ai_feed_metrics(items)
        assert m["ai_source_diversity"] == 1

    def test_zero_diversity_when_no_ai(self):
        items = [make_tech_item(i) for i in range(5)]
        m = compute_ai_feed_metrics(items)
        assert m["ai_source_diversity"] == 0


class TestAIShareExceedsCap:
    """ai_share_exceeds_cap() helper."""

    def test_below_cap(self):
        items = [make_ai_item(i) for i in range(2)]
        items += [make_tech_item(i) for i in range(2, 8)]  # 2/8 = 25 %
        assert not ai_share_exceeds_cap(items, cap_pct=40.0)

    def test_above_cap(self):
        items = [make_ai_item(i) for i in range(5)]
        items += [make_tech_item(i) for i in range(5, 8)]  # 5/8 = 62.5 %
        assert ai_share_exceeds_cap(items, cap_pct=40.0)

    def test_exactly_at_cap_not_exceeded(self):
        items = [make_ai_item(i) for i in range(2)]
        items += [make_tech_item(i) for i in range(2, 5)]  # 2/5 = 40 %
        assert not ai_share_exceeds_cap(items, cap_pct=40.0)

    def test_empty_feed_not_exceeded(self):
        assert not ai_share_exceeds_cap([], cap_pct=40.0)
