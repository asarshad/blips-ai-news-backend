"""Unit tests for P6-4: semantic story dedup via entity-Jaccard.

Covers:
- _is_semantic_story_duplicate() helper
- Edge cases: too few entities, empty sets, threshold boundary
- FP guards: different stories sharing one entity, broader entity sets
"""

from __future__ import annotations

import re

import pytest

from app.services.promotion_service import (
    _SEMANTIC_DEDUP_ENTITY_JACCARD_THRESHOLD,
    _SEMANTIC_DEDUP_MIN_SHARED_ENTITIES,
    _is_semantic_story_duplicate,
    _normalize_entities_for_dedup,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_seen_pair(title: str, entities: list) -> tuple[frozenset[str], frozenset[str]]:
    tokens = frozenset(re.findall(r"\\w+", title.lower()))
    entity_set = _normalize_entities_for_dedup(entities)
    return (tokens, entity_set)


# ---------------------------------------------------------------------------
# Core behaviour: should match (True)
# ---------------------------------------------------------------------------


class TestSemanticDedup_ShouldMatch:
    def test_exact_entity_match_is_duplicate(self):
        """Two articles with identical entity sets → caught."""
        seen = [_make_seen_pair("OpenAI launches Codex", ["OpenAI", "Codex"])]
        assert _is_semantic_story_duplicate(["OpenAI", "Codex"], seen)

    def test_musk_altman_lawsuit_different_vocabulary(self):
        """Canonical P4-1 failure: 4-article Musk/Altman cluster."""
        seen = [
            _make_seen_pair(
                "Elon Musk files lawsuit against Sam Altman over OpenAI",
                ["Elon Musk", "Sam Altman", "OpenAI"],
            )
        ]
        # Different verb + structure, same 3 entities — should be caught
        assert _is_semantic_story_duplicate(
            ["Elon Musk", "Sam Altman", "OpenAI"],
            seen,
        )

    def test_openai_codex_cluster_different_vocabulary(self):
        """Canonical P4-1 failure: 3-article OpenAI Codex cluster."""
        seen = [
            _make_seen_pair(
                "OpenAI's Codex agent can autonomously write code",
                ["OpenAI", "Codex"],
            )
        ]
        assert _is_semantic_story_duplicate(["OpenAI", "Codex"], seen)

    def test_superset_entities_still_matches_when_jaccard_sufficient(self):
        """Seen article has 3 entities, candidate has 2 that both overlap."""
        # seen: {openai, codex, api}; candidate: {openai, codex}
        # Jaccard = 2/3 ≈ 0.67 ≥ 0.50 → match
        seen = [
            _make_seen_pair(
                "OpenAI Codex available via API",
                ["OpenAI", "Codex", "API"],
            )
        ]
        assert _is_semantic_story_duplicate(["OpenAI", "Codex"], seen)

    def test_three_way_entity_overlap_matches(self):
        """3 shared out of 3 total entities — entity Jaccard = 1.0."""
        seen = [
            _make_seen_pair(
                "Google acquires DeepMind spinoff for $1B",
                ["Google", "DeepMind", "AI"],
            )
        ]
        assert _is_semantic_story_duplicate(["Google", "DeepMind", "AI"], seen)

    def test_short_circuits_on_first_match(self):
        """Returns True on the first matching pair without scanning the rest."""
        seen = [
            _make_seen_pair("OpenAI launches Codex", ["OpenAI", "Codex"]),
            _make_seen_pair("Apple announces M5 chip", ["Apple", "M5"]),
        ]
        assert _is_semantic_story_duplicate(["OpenAI", "Codex"], seen)


# ---------------------------------------------------------------------------
# FP guards: should NOT match (False)
# ---------------------------------------------------------------------------


class TestSemanticDedup_ShouldNotMatch:
    def test_only_one_shared_entity_no_match(self):
        """Two different OpenAI stories: GPT-5 launch vs. funding round."""
        seen = [_make_seen_pair("OpenAI GPT-5 launches", ["OpenAI", "GPT-5"])]
        # funding article only shares "OpenAI" — 1 shared entity < 2
        assert not _is_semantic_story_duplicate(["OpenAI", "SoftBank"], seen)

    def test_entity_jaccard_below_threshold_no_match(self):
        """Enough shared entities but the overall Jaccard is too low."""
        # seen: {apple, ios, swift, xcode} — 4 entities
        # candidate: {apple, ios, macos} — 3 entities
        # shared: {apple, ios} = 2; union = 5; Jaccard = 0.40 < 0.50
        seen = [
            _make_seen_pair(
                "Apple WWDC announces new Swift and Xcode updates",
                ["Apple", "iOS", "Swift", "Xcode"],
            )
        ]
        assert not _is_semantic_story_duplicate(["Apple", "iOS", "macOS"], seen)

    def test_different_stories_same_broad_entity(self):
        """Two unrelated Apple stories: earnings vs. new product."""
        seen = [
            _make_seen_pair(
                "Apple reports record Q1 earnings",
                ["Apple", "Wall Street"],
            )
        ]
        assert not _is_semantic_story_duplicate(["Apple", "iPhone", "Vision Pro"], seen)

    def test_candidate_has_too_few_entities_skipped(self):
        """Candidate with <2 entities cannot satisfy the shared-entity floor."""
        seen = [_make_seen_pair("OpenAI launches Codex agent", ["OpenAI", "Codex"])]
        # Only 1 entity on candidate
        assert not _is_semantic_story_duplicate(["OpenAI"], seen)

    def test_empty_candidate_entities(self):
        seen = [_make_seen_pair("OpenAI launches Codex", ["OpenAI", "Codex"])]
        assert not _is_semantic_story_duplicate([], seen)

    def test_none_candidate_entities(self):
        seen = [_make_seen_pair("OpenAI launches Codex", ["OpenAI", "Codex"])]
        assert not _is_semantic_story_duplicate(None, seen)

    def test_empty_seen_pairs(self):
        assert not _is_semantic_story_duplicate(["OpenAI", "Codex"], [])

    def test_seen_pair_with_no_entities_is_skipped(self):
        """Seen item with empty entity set never triggers a match."""
        seen = [_make_seen_pair("OpenAI launches Codex", [])]
        assert not _is_semantic_story_duplicate(["OpenAI", "Codex"], seen)

    def test_completely_different_entities_no_match(self):
        seen = [_make_seen_pair("Apple acquires Shazam", ["Apple", "Shazam"])]
        assert not _is_semantic_story_duplicate(["Google", "DeepMind", "AI"], seen)


# ---------------------------------------------------------------------------
# Threshold boundary tests
# ---------------------------------------------------------------------------


class TestSemanticDedup_Threshold:
    def test_exactly_at_threshold_matches(self):
        """entity Jaccard == threshold should fire (>=)."""
        # 2 shared / 4 union = 0.50 exactly
        seen = [
            _make_seen_pair(
                "OpenAI Codex agent demo",
                ["OpenAI", "Codex", "GitHub", "Microsoft"],
            )
        ]
        # candidate {OpenAI, Codex}: shared=2, union={OpenAI,Codex,GitHub,Microsoft}=4 → 0.50
        result = _is_semantic_story_duplicate(
            ["OpenAI", "Codex"],
            seen,
            entity_jaccard_threshold=0.50,
        )
        assert result is True

    def test_just_below_threshold_no_match(self):
        """entity Jaccard just below threshold must not fire."""
        # 2/5 = 0.40 < 0.50
        seen = [
            _make_seen_pair(
                "Apple WWDC updates",
                ["Apple", "iOS", "Swift", "Xcode", "macOS"],
            )
        ]
        result = _is_semantic_story_duplicate(
            ["Apple", "iOS"],
            seen,
            entity_jaccard_threshold=0.50,
        )
        assert result is False

    def test_custom_min_shared_entities_respected(self):
        """min_shared_entities=3 requires 3 overlapping entities."""
        seen = [
            _make_seen_pair(
                "OpenAI Codex agent launch",
                ["OpenAI", "Codex", "GitHub"],
            )
        ]
        # Only 2 shared with candidate {OpenAI, Codex}
        assert not _is_semantic_story_duplicate(
            ["OpenAI", "Codex"],
            seen,
            min_shared_entities=3,
        )
        # 3 shared entities → match
        assert _is_semantic_story_duplicate(
            ["OpenAI", "Codex", "GitHub"],
            seen,
            min_shared_entities=3,
        )

    def test_default_constants_exported(self):
        """Confirm module-level defaults are accessible for tests."""
        assert _SEMANTIC_DEDUP_MIN_SHARED_ENTITIES == 2
        assert _SEMANTIC_DEDUP_ENTITY_JACCARD_THRESHOLD == pytest.approx(0.50)
