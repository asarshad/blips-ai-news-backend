"""Unit tests for cross-source title dedup helpers (P2-1)."""

from __future__ import annotations

import pytest

from app.services.promotion_service import (
    _CROSS_SOURCE_DEDUP_SIMILARITY_THRESHOLD,
    _is_cross_source_duplicate,
    _normalize_entities_for_dedup,
    _title_token_similarity,
)

# ---------------------------------------------------------------------------
# _title_token_similarity
# ---------------------------------------------------------------------------


class TestTitleTokenSimilarity:
    def test_identical_titles_score_one(self):
        assert _title_token_similarity("Apple announces new Mac", "Apple announces new Mac") == 1.0

    def test_completely_different_titles_score_zero(self):
        sim = _title_token_similarity("Apple launches iPhone", "Samsung releases Galaxy")
        assert sim == 0.0

    def test_high_overlap_near_duplicate(self):
        a = "Apple announces new MacBook Pro with M4 chip"
        b = "Apple announces MacBook Pro refresh with M4 chip"
        sim = _title_token_similarity(a, b)
        assert sim >= 0.75

    def test_synonym_swap_scores_below_threshold(self):
        # "announces" vs "unveils" — different words, same meaning
        a = "Apple announces new MacBook Pro"
        b = "Apple unveils new MacBook Pro"
        sim = _title_token_similarity(a, b)
        # Jaccard should be < 0.75 with one-word swap over a 5-word title
        assert sim < 0.75

    def test_empty_string_returns_zero(self):
        assert _title_token_similarity("", "Apple announces Mac") == 0.0
        assert _title_token_similarity("Apple announces Mac", "") == 0.0
        assert _title_token_similarity("", "") == 0.0

    def test_case_insensitive(self):
        assert _title_token_similarity("APPLE MAC PRO", "apple mac pro") == 1.0

    def test_partial_overlap_scores_correctly(self):
        a = "OpenAI releases GPT-5 with new capabilities"
        b = "OpenAI GPT-5 is now available to all users"
        sim = _title_token_similarity(a, b)
        assert 0.0 < sim < 1.0

    def test_single_token_mismatch(self):
        # Same title except one word
        a = "Google releases Gemini 2.0 update"
        b = "Google releases Gemini 2.0 model"
        sim = _title_token_similarity(a, b)
        # 4 shared tokens out of 6 total = 0.667
        assert pytest.approx(sim, abs=0.05) == 4 / 6


# ---------------------------------------------------------------------------
# _normalize_entities_for_dedup
# ---------------------------------------------------------------------------


class TestNormalizeEntitiesForDedup:
    def test_plain_string_list(self):
        result = _normalize_entities_for_dedup(["Apple", "OpenAI", "GPT-5"])
        assert result == frozenset({"apple", "openai", "gpt-5"})

    def test_dict_list_with_name_key(self):
        entities = [{"name": "Apple"}, {"name": "OpenAI"}]
        result = _normalize_entities_for_dedup(entities)
        assert result == frozenset({"apple", "openai"})

    def test_empty_list_returns_empty_frozenset(self):
        assert _normalize_entities_for_dedup([]) == frozenset()

    def test_non_list_returns_empty_frozenset(self):
        assert _normalize_entities_for_dedup(None) == frozenset()
        assert _normalize_entities_for_dedup("apple") == frozenset()

    def test_mixed_types_skips_non_string_non_dict(self):
        result = _normalize_entities_for_dedup(["Apple", 42, None, {"name": "OpenAI"}])
        assert result == frozenset({"apple", "openai"})

    def test_duplicates_deduplicated(self):
        result = _normalize_entities_for_dedup(["Apple", "apple", "APPLE"])
        assert result == frozenset({"apple"})


# ---------------------------------------------------------------------------
# _is_cross_source_duplicate
# ---------------------------------------------------------------------------


def _make_seen_pair(title: str, entities: list) -> tuple[frozenset[str], frozenset[str]]:
    import re

    tokens = frozenset(re.findall(r"\w+", title.lower()))
    entity_set = _normalize_entities_for_dedup(entities)
    return (tokens, entity_set)


class TestIsCrossSourceDuplicate:
    def test_exact_match_is_duplicate(self):
        seen = [
            _make_seen_pair(
                "Apple announces new MacBook Pro with M4 chip",
                ["Apple", "MacBook Pro", "M4"],
            )
        ]
        assert _is_cross_source_duplicate(
            "Apple announces new MacBook Pro with M4 chip",
            ["Apple", "MacBook Pro", "M4"],
            seen,
        )

    def test_near_duplicate_above_threshold_is_suppressed(self):
        seen = [
            _make_seen_pair(
                "Apple announces new MacBook Pro with M4 chip",
                ["Apple", "MacBook Pro", "M4"],
            )
        ]
        # Small wording change, same entities
        assert _is_cross_source_duplicate(
            "Apple announces MacBook Pro refresh with M4 chip",
            ["Apple", "MacBook Pro", "M4"],
            seen,
        )

    def test_same_title_but_no_shared_entity_is_not_duplicate(self):
        seen = [
            _make_seen_pair(
                "Apple announces new MacBook Pro with M4 chip",
                ["Samsung", "Galaxy"],  # completely different entities
            )
        ]
        assert not _is_cross_source_duplicate(
            "Apple announces new MacBook Pro with M4 chip",
            ["Apple", "MacBook Pro"],
            seen,
        )

    def test_shared_entity_but_low_title_similarity_is_not_duplicate(self):
        seen = [
            _make_seen_pair(
                "Apple reports record quarterly earnings amid tariff uncertainty",
                ["Apple"],
            )
        ]
        # Apple entity shared but titles are very different
        assert not _is_cross_source_duplicate(
            "Apple unveils new iPhone 17 lineup with camera improvements",
            ["Apple"],
            seen,
        )

    def test_no_entities_on_candidate_never_suppressed(self):
        """Items with no entities cannot be verified — skip dedup."""
        seen = [
            _make_seen_pair(
                "Apple announces new MacBook Pro with M4 chip",
                ["Apple", "MacBook Pro"],
            )
        ]
        assert not _is_cross_source_duplicate(
            "Apple announces new MacBook Pro with M4 chip",
            [],  # no entities
            seen,
        )

    def test_no_entities_on_seen_item_not_matched(self):
        """Seen item with no entities can't provide overlap — skip it."""
        seen = [
            _make_seen_pair(
                "Apple announces new MacBook Pro with M4 chip",
                [],  # no entities
            )
        ]
        assert not _is_cross_source_duplicate(
            "Apple announces new MacBook Pro with M4 chip",
            ["Apple", "MacBook Pro"],
            seen,
        )

    def test_empty_seen_pairs_never_suppressed(self):
        assert not _is_cross_source_duplicate(
            "Apple announces new MacBook Pro",
            ["Apple"],
            [],
        )

    def test_none_title_never_suppressed(self):
        seen = [_make_seen_pair("Apple announces new MacBook Pro", ["Apple"])]
        assert not _is_cross_source_duplicate(None, ["Apple"], seen)

    def test_empty_title_never_suppressed(self):
        seen = [_make_seen_pair("Apple announces new MacBook Pro", ["Apple"])]
        assert not _is_cross_source_duplicate("", ["Apple"], seen)

    def test_custom_threshold_respected(self):
        """Lower threshold catches more duplicates."""
        seen = [
            _make_seen_pair(
                "Apple announces new MacBook Pro",
                ["Apple", "MacBook Pro"],
            )
        ]
        # Synonym swap: "announces" → "unveils"
        # At default 0.75 threshold this should NOT be a duplicate
        assert not _is_cross_source_duplicate(
            "Apple unveils new MacBook Pro",
            ["Apple", "MacBook Pro"],
            seen,
            threshold=_CROSS_SOURCE_DEDUP_SIMILARITY_THRESHOLD,
        )
        # But at a lower threshold it should be
        assert _is_cross_source_duplicate(
            "Apple unveils new MacBook Pro",
            ["Apple", "MacBook Pro"],
            seen,
            threshold=0.50,
        )

    def test_short_circuits_on_first_match(self):
        """Should return True immediately when first matching pair found."""
        seen = [
            _make_seen_pair("Apple announces new MacBook Pro with M4", ["Apple", "MacBook Pro"]),
            _make_seen_pair("Google releases Pixel 9", ["Google", "Pixel 9"]),
        ]
        # First pair matches — should return True without checking second
        result = _is_cross_source_duplicate(
            "Apple announces new MacBook Pro with M4",
            ["Apple", "MacBook Pro"],
            seen,
        )
        assert result is True

    def test_false_positive_guard_different_stories_same_entity(self):
        """Different Apple stories should NOT be suppressed."""
        seen = [
            _make_seen_pair(
                "Apple reports record Q1 earnings beating expectations",
                ["Apple", "Wall Street"],
            )
        ]
        assert not _is_cross_source_duplicate(
            "Apple introduces new accessibility features for iOS 19",
            ["Apple", "iOS 19"],
            seen,
        )

    def test_openai_gpt5_near_duplicate(self):
        """Typical real-world near-duplicate: same story, slightly different headline."""
        seen = [
            _make_seen_pair(
                "OpenAI launches GPT-5 with improved reasoning and multimodal capabilities",
                ["OpenAI", "GPT-5"],
            )
        ]
        assert _is_cross_source_duplicate(
            "OpenAI launches GPT-5 featuring improved reasoning and multimodal capabilities",
            ["OpenAI", "GPT-5"],
            seen,
        )
