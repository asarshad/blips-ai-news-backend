"""Unit tests for P3-2: entity category floor in PlaylistService.

Covers:
- _item_entity_names() helper
- _apply_entity_floor() core logic
- Floor fires: missing entity with qualifying candidate
- Floor skips: entity already present, no qualifying candidate, score too low
- Multi-entity: two floor entities both missing
- Window boundary: only checks first ENTITY_FLOOR_CHECK_WINDOW positions
- List length preserved after swap
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

from app.models.content import ContentItem, ContentType
from app.services.playlist_service import (
    ENTITY_FLOOR_CHECK_WINDOW,
    ENTITY_FLOOR_ENTITIES,
    ENTITY_FLOOR_MIN_SCORE,
    PlaylistService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service() -> PlaylistService:
    return PlaylistService(
        content_repo=Mock(),
        profile_repo=Mock(),
        preference_repo=Mock(),
        personalization_service=Mock(),
        redis_client=None,
    )


def _item(
    item_id: int,
    *,
    entities: list[str] | None = None,
    global_score: float = 0.50,
    source: str = "TechCrunch",
) -> ContentItem:
    return ContentItem(
        id=item_id,
        type=ContentType.ARTICLE,
        title=f"Headline {item_id}",
        source=source,
        source_url=f"https://example.com/{item_id}",
        published_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        topics=["ai"],
        entities=entities or [],
        global_score=global_score,
        cluster_id=f"cluster-{item_id}",
    )


# ---------------------------------------------------------------------------
# _item_entity_names
# ---------------------------------------------------------------------------


class TestItemEntityNames:
    def test_plain_string_list(self):
        item = _item(1, entities=["Apple", "iPhone"])
        assert PlaylistService._item_entity_names(item) == {"apple", "iphone"}

    def test_dict_list_with_name_key(self):
        item = _item(1, entities=[{"name": "Apple"}, {"name": "OpenAI"}])
        assert PlaylistService._item_entity_names(item) == {"apple", "openai"}

    def test_empty_entities(self):
        item = _item(1, entities=[])
        assert PlaylistService._item_entity_names(item) == frozenset()

    def test_none_entities(self):
        item = _item(1, entities=None)
        assert PlaylistService._item_entity_names(item) == frozenset()

    def test_case_normalised(self):
        item = _item(1, entities=["MICROSOFT", "Meta"])
        assert PlaylistService._item_entity_names(item) == {"microsoft", "meta"}


# ---------------------------------------------------------------------------
# _apply_entity_floor — floor fires
# ---------------------------------------------------------------------------


class TestApplyEntityFloor_Fires:
    def test_missing_apple_injects_apple_item(self):
        """Apple absent from window; qualifying candidate available → injected."""
        svc = _service()
        # 3 items, none with Apple entity
        selected = [
            _item(1, entities=["openai"], global_score=0.70),
            _item(2, entities=["google"], global_score=0.65),
            _item(3, entities=["nvidia"], global_score=0.60),
        ]
        apple_item = _item(99, entities=["apple"], global_score=0.55)
        candidates = selected + [apple_item]

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple"}),
            check_window=3,
        )

        assert len(result) == 3  # Size preserved
        result_ids = [item.id for item in result]
        assert 99 in result_ids, "Apple item should be in result"

    def test_lowest_scored_window_item_is_replaced(self):
        """The swap target is the lowest global_score item in the window."""
        svc = _service()
        selected = [
            _item(1, entities=["openai"], global_score=0.80),
            _item(2, entities=["google"], global_score=0.75),
            _item(3, entities=["nvidia"], global_score=0.40),  # weakest
        ]
        apple_item = _item(99, entities=["apple"], global_score=0.50)
        candidates = selected + [apple_item]

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple"}),
            check_window=3,
        )

        result_ids = [item.id for item in result]
        assert 3 not in result_ids, "Weakest item (id=3) should be displaced"
        assert 99 in result_ids

    def test_two_missing_floor_entities_both_injected(self):
        """Both Apple and Microsoft missing — best candidates for each are injected."""
        svc = _service()
        selected = [
            _item(1, entities=["openai"], global_score=0.80),
            _item(2, entities=["google"], global_score=0.75),
            _item(3, entities=["nvidia"], global_score=0.50),
            _item(4, entities=["amazon"], global_score=0.45),
        ]
        apple_item = _item(91, entities=["apple"], global_score=0.55)
        msft_item = _item(92, entities=["microsoft"], global_score=0.52)
        candidates = selected + [apple_item, msft_item]

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple", "microsoft"}),
            check_window=4,
        )

        assert len(result) == 4
        result_ids = {item.id for item in result}
        assert 91 in result_ids
        assert 92 in result_ids

    def test_best_floor_candidate_chosen_when_multiple_available(self):
        """When two candidates carry the same floor entity, the higher-scored one wins."""
        svc = _service()
        selected = [_item(1, entities=["openai"], global_score=0.80)]
        apple_low = _item(91, entities=["apple"], global_score=0.40)
        apple_high = _item(92, entities=["apple"], global_score=0.65)
        candidates = selected + [apple_low, apple_high]

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple"}),
            check_window=1,
        )

        result_ids = [item.id for item in result]
        assert 92 in result_ids, "Higher-scored Apple candidate should be chosen"
        assert 91 not in result_ids


# ---------------------------------------------------------------------------
# _apply_entity_floor — floor skips (no-op)
# ---------------------------------------------------------------------------


class TestApplyEntityFloor_Skips:
    def test_entity_already_present_no_change(self):
        """Apple already in window — floor is no-op."""
        svc = _service()
        selected = [
            _item(1, entities=["apple"], global_score=0.80),
            _item(2, entities=["openai"], global_score=0.70),
        ]
        apple_candidate = _item(99, entities=["apple"], global_score=0.55)
        candidates = selected + [apple_candidate]

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple"}),
            check_window=2,
        )

        assert result == selected  # Unchanged

    def test_no_qualifying_candidate_score_too_low(self):
        """Floor candidate exists but global_score < ENTITY_FLOOR_MIN_SCORE → skip."""
        svc = _service()
        selected = [_item(1, entities=["openai"], global_score=0.80)]
        apple_junk = _item(99, entities=["apple"], global_score=0.10)
        candidates = selected + [apple_junk]

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple"}),
            check_window=1,
            min_score=0.30,
        )

        assert result == selected

    def test_no_candidate_with_floor_entity_skip(self):
        """No candidate has the floor entity at all — list unchanged."""
        svc = _service()
        selected = [_item(1, entities=["openai"], global_score=0.80)]
        non_apple = _item(99, entities=["google"], global_score=0.70)
        candidates = selected + [non_apple]

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple"}),
            check_window=1,
        )

        assert result == selected

    def test_empty_selected_returns_empty(self):
        svc = _service()
        candidates = [_item(1, entities=["apple"], global_score=0.80)]
        result = svc._apply_entity_floor([], candidates)
        assert result == []

    def test_empty_candidates_returns_unchanged(self):
        svc = _service()
        selected = [_item(1, entities=["openai"], global_score=0.80)]
        result = svc._apply_entity_floor(selected, [])
        assert result == selected

    def test_floor_candidate_already_in_selected_not_used(self):
        """Candidate already selected should NOT be used as a floor swap."""
        svc = _service()
        apple_item = _item(99, entities=["apple"], global_score=0.80)
        selected = [apple_item]
        candidates = [apple_item]  # Same item

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple"}),
            check_window=1,
        )

        # Already present → no swap needed
        assert result == selected


# ---------------------------------------------------------------------------
# Window boundary
# ---------------------------------------------------------------------------


class TestApplyEntityFloor_Window:
    def test_floor_checks_only_first_n_positions(self):
        """Entity present in position 16 does NOT satisfy a window=15 floor."""
        svc = _service()
        # 16 items: positions 0-14 have no Apple, position 15 has Apple
        selected = [_item(i, entities=["openai"], global_score=0.80 - i * 0.01) for i in range(15)]
        apple_in_tail = _item(100, entities=["apple"], global_score=0.65)
        selected.append(apple_in_tail)  # position 15 — outside window

        apple_candidate = _item(200, entities=["apple"], global_score=0.50)
        candidates = selected + [apple_candidate]

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple"}),
            check_window=15,
        )

        result_ids = {item.id for item in result}
        # Floor candidate (200) should be injected because Apple wasn't in first 15
        assert 200 in result_ids

    def test_entity_in_position_within_window_satisfies_floor(self):
        """Entity in position 14 (0-indexed) satisfies window=15 floor."""
        svc = _service()
        selected = [_item(i, entities=["openai"], global_score=0.80 - i * 0.01) for i in range(14)]
        apple_in_window = _item(99, entities=["apple"], global_score=0.66)
        selected.append(apple_in_window)  # position 14 — inside 15-item window

        apple_candidate = _item(200, entities=["apple"], global_score=0.50)
        candidates = selected + [apple_candidate]

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple"}),
            check_window=15,
        )

        assert len(result) == 15
        # Apple is already covered — no injection needed
        result_ids = {item.id for item in result}
        assert 200 not in result_ids

    def test_list_length_always_preserved(self):
        """Output list is always the same length as input."""
        svc = _service()
        selected = [_item(i, entities=["openai"], global_score=0.80 - i * 0.02) for i in range(20)]
        apple_item = _item(99, entities=["apple"], global_score=0.55)
        candidates = selected + [apple_item]

        result = svc._apply_entity_floor(
            selected,
            candidates,
            floor_entities=frozenset({"apple"}),
            check_window=15,
        )

        assert len(result) == 20


# ---------------------------------------------------------------------------
# Module-level constant sanity checks
# ---------------------------------------------------------------------------


def test_floor_constants_exported():
    assert "apple" in ENTITY_FLOOR_ENTITIES
    assert "microsoft" in ENTITY_FLOOR_ENTITIES
    assert "meta" in ENTITY_FLOOR_ENTITIES
    assert ENTITY_FLOOR_CHECK_WINDOW == 15
    assert ENTITY_FLOOR_MIN_SCORE == 0.30
