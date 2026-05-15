"""Unit tests for P3-1: premium-source co-mention boost.

Covers:
- compute_global_score() with co_mention_boost parameter
- ScoringService._build_cluster_source_map()
- ScoringService._co_mention_boost_for_item()
- ScoringService.run_scoring_job() stats include co_mention_boosted count
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.ranking.global_score import CO_MENTION_SCORE_BOOST, compute_global_score
from app.ranking.service import ScoringService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    item_id: int = 1,
    source: str = "TechCrunch",
    cluster_id: str | None = None,
    is_major_tech_news: bool = False,
    editorial_boost: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        source=source,
        cluster_id=cluster_id,
        type=None,
        title="Test headline",
        summary="Test summary",
        description="Test description",
        image_url=None,
        topics=[],
        entities=[],
        tech_relevance_confidence=None,
        published_at=None,
        is_major_tech_news=is_major_tech_news,
        editorial_boost=editorial_boost,
    )


PREMIUM = {"TechCrunch", "The Verge", "Wired", "Ars Technica"}


# ---------------------------------------------------------------------------
# compute_global_score — co_mention_boost parameter
# ---------------------------------------------------------------------------


class TestComputeGlobalScoreCoMention:
    def test_zero_boost_unchanged(self):
        base = compute_global_score(
            quality_score=0.6,
            trend_score=0.5,
            recency_score=0.7,
            co_mention_boost=0.0,
        )
        no_kwarg = compute_global_score(
            quality_score=0.6,
            trend_score=0.5,
            recency_score=0.7,
        )
        assert base == pytest.approx(no_kwarg)

    def test_co_mention_boost_is_additive(self):
        without = compute_global_score(
            quality_score=0.6,
            trend_score=0.5,
            recency_score=0.7,
            co_mention_boost=0.0,
        )
        with_boost = compute_global_score(
            quality_score=0.6,
            trend_score=0.5,
            recency_score=0.7,
            co_mention_boost=CO_MENTION_SCORE_BOOST,
        )
        assert with_boost == pytest.approx(without + CO_MENTION_SCORE_BOOST)

    def test_co_mention_boost_default_is_zero(self):
        """Default value doesn't silently apply boost."""
        score = compute_global_score(quality_score=0.5, trend_score=0.5, recency_score=0.5)
        score_explicit = compute_global_score(
            quality_score=0.5, trend_score=0.5, recency_score=0.5, co_mention_boost=0.0
        )
        assert score == pytest.approx(score_explicit)

    def test_score_does_not_exceed_clamp(self):
        """All boosts at max should still be clamped."""
        score = compute_global_score(
            quality_score=1.0,
            trend_score=1.0,
            recency_score=1.0,
            editorial_boost=3,
            is_major_tech_news=True,
            co_mention_boost=CO_MENTION_SCORE_BOOST,
        )
        assert score <= 1.0 + 3 * 0.05 + 0.12 + CO_MENTION_SCORE_BOOST + 1e-9


# ---------------------------------------------------------------------------
# ScoringService._build_cluster_source_map
# ---------------------------------------------------------------------------


class TestBuildClusterSourceMap:
    def test_groups_items_by_cluster(self):
        items = [
            _make_item(1, source="TechCrunch", cluster_id="cluster-A"),
            _make_item(2, source="The Verge", cluster_id="cluster-A"),
            _make_item(3, source="Wired", cluster_id="cluster-B"),
        ]
        result = ScoringService._build_cluster_source_map(items)
        assert result["cluster-A"] == {"TechCrunch", "The Verge"}
        assert result["cluster-B"] == {"Wired"}

    def test_items_without_cluster_ignored(self):
        items = [
            _make_item(1, source="TechCrunch", cluster_id=None),
            _make_item(2, source="The Verge", cluster_id="cluster-A"),
        ]
        result = ScoringService._build_cluster_source_map(items)
        assert "None" not in result
        assert None not in result
        assert "cluster-A" in result

    def test_items_without_source_ignored(self):
        items = [
            _make_item(1, source="", cluster_id="cluster-A"),
            _make_item(2, source="TechCrunch", cluster_id="cluster-A"),
        ]
        result = ScoringService._build_cluster_source_map(items)
        assert "" not in result.get("cluster-A", set())
        assert "TechCrunch" in result["cluster-A"]

    def test_empty_list_returns_empty_dict(self):
        assert ScoringService._build_cluster_source_map([]) == {}


# ---------------------------------------------------------------------------
# ScoringService._co_mention_boost_for_item
# ---------------------------------------------------------------------------


class TestCoMentionBoostForItem:
    def test_no_cluster_id_returns_zero(self):
        item = _make_item(cluster_id=None)
        result = ScoringService._co_mention_boost_for_item(item, {}, PREMIUM)
        assert result == 0.0

    def test_cluster_with_one_premium_source_no_boost(self):
        item = _make_item(source="TechCrunch", cluster_id="cluster-A")
        cluster_map = {"cluster-A": {"TechCrunch", "ZDNet"}}  # only 1 PREMIUM
        result = ScoringService._co_mention_boost_for_item(item, cluster_map, PREMIUM)
        assert result == 0.0

    def test_cluster_with_two_premium_sources_boosts(self):
        item = _make_item(source="TechCrunch", cluster_id="cluster-A")
        cluster_map = {"cluster-A": {"TechCrunch", "The Verge", "ZDNet"}}  # 2 PREMIUM
        result = ScoringService._co_mention_boost_for_item(item, cluster_map, PREMIUM)
        assert result == pytest.approx(CO_MENTION_SCORE_BOOST)

    def test_cluster_with_three_premium_sources_also_boosts(self):
        item = _make_item(source="Wired", cluster_id="cluster-A")
        cluster_map = {"cluster-A": {"TechCrunch", "The Verge", "Wired"}}  # 3 PREMIUM
        result = ScoringService._co_mention_boost_for_item(item, cluster_map, PREMIUM)
        assert result == pytest.approx(CO_MENTION_SCORE_BOOST)

    def test_cluster_with_only_non_premium_sources_no_boost(self):
        item = _make_item(source="ZDNet", cluster_id="cluster-A")
        cluster_map = {"cluster-A": {"ZDNet", "CNET", "TechRadar"}}  # 0 PREMIUM
        result = ScoringService._co_mention_boost_for_item(item, cluster_map, PREMIUM)
        assert result == 0.0

    def test_item_cluster_not_in_map_returns_zero(self):
        item = _make_item(cluster_id="cluster-missing")
        result = ScoringService._co_mention_boost_for_item(item, {}, PREMIUM)
        assert result == 0.0

    def test_empty_premium_set_never_boosts(self):
        item = _make_item(source="TechCrunch", cluster_id="cluster-A")
        cluster_map = {"cluster-A": {"TechCrunch", "The Verge"}}
        result = ScoringService._co_mention_boost_for_item(item, cluster_map, frozenset())
        assert result == 0.0

    def test_custom_min_premium_sources(self):
        """min_premium_sources=3 requires at least 3 PREMIUM sources."""
        item = _make_item(source="TechCrunch", cluster_id="cluster-A")
        cluster_map = {"cluster-A": {"TechCrunch", "The Verge"}}  # only 2
        result = ScoringService._co_mention_boost_for_item(
            item, cluster_map, PREMIUM, min_premium_sources=3
        )
        assert result == 0.0

        cluster_map["cluster-A"].add("Wired")  # now 3
        result = ScoringService._co_mention_boost_for_item(
            item, cluster_map, PREMIUM, min_premium_sources=3
        )
        assert result == pytest.approx(CO_MENTION_SCORE_BOOST)


# ---------------------------------------------------------------------------
# ScoringService.run_scoring_job — co_mention_boosted stat
# ---------------------------------------------------------------------------


class TestRunScoringJobCoMentionStat:
    def _make_service(self, items: list[Any]) -> tuple[ScoringService, list[dict]]:
        """Return (service, score_updates) where score_updates collects update_scores calls."""
        score_updates: list[dict] = []

        content_repo = MagicMock()
        content_repo.get_recent_with_scores.return_value = items
        content_repo.get_by_type.return_value = []
        content_repo.get_cluster_size.return_value = len(items)

        def _fake_update_scores(item_id, **kwargs):
            score_updates.append({"id": item_id, **kwargs})
            return True

        content_repo.update_scores.side_effect = _fake_update_scores

        interaction_repo = MagicMock()
        interaction_repo.get_events_for_content.return_value = []

        svc = ScoringService(content_repo, interaction_repo)
        return svc, score_updates

    def test_no_clusters_co_mention_boosted_is_zero(self):
        items = [_make_item(1, cluster_id=None), _make_item(2, cluster_id=None)]
        svc, _ = self._make_service(items)
        result = svc.run_scoring_job()
        assert result["co_mention_boosted"] == 0

    def test_qualifying_cluster_increments_stat(self, monkeypatch):
        items = [
            _make_item(1, source="TechCrunch", cluster_id="story-1"),
            _make_item(2, source="The Verge", cluster_id="story-1"),
            _make_item(3, source="ZDNet", cluster_id=None),
        ]
        svc, _ = self._make_service(items)
        # Patch _build_premium_source_names to return controlled set
        monkeypatch.setattr(
            ScoringService,
            "_build_premium_source_names",
            staticmethod(lambda: frozenset({"TechCrunch", "The Verge"})),
        )
        result = svc.run_scoring_job()
        # Items 1 and 2 are in a cluster with 2 PREMIUM sources → both boosted
        assert result["co_mention_boosted"] == 2

    def test_boosted_items_have_higher_global_score(self, monkeypatch):
        """Items in qualifying cluster should receive a higher global_score."""
        items = [
            _make_item(1, source="TechCrunch", cluster_id="story-1"),
            _make_item(2, source="The Verge", cluster_id="story-1"),
            _make_item(3, source="ZDNet", cluster_id=None),
        ]
        svc, score_updates = self._make_service(items)
        monkeypatch.setattr(
            ScoringService,
            "_build_premium_source_names",
            staticmethod(lambda: frozenset({"TechCrunch", "The Verge"})),
        )
        svc.run_scoring_job()

        boosted_ids = {1, 2}
        unboosted_ids = {3}

        boosted_scores = [u["global_score"] for u in score_updates if u["id"] in boosted_ids]
        unboosted_scores = [u["global_score"] for u in score_updates if u["id"] in unboosted_ids]

        # Each boosted item's score should exceed its unboosted equivalent
        # (same base inputs, only difference is co_mention_boost)
        for b in boosted_scores:
            for u in unboosted_scores:
                assert b > u - 1e-9, (
                    f"Boosted score {b:.4f} should be above unboosted {u:.4f} "
                    f"(difference at least CO_MENTION_SCORE_BOOST={CO_MENTION_SCORE_BOOST})"
                )
