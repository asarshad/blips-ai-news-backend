"""Tests for position-based channel diversity caps."""

from app.services.diversity_mixer import enforce_channel_caps


def _item(id: int, channel: str) -> dict:
    return {"id": id, "channel_id": channel, "source": channel}


def test_videos_max_2_from_same_channel_in_first_20():
    # 5 ch-A items followed by 25 unique channel items (30 total)
    items = [_item(i, "ch-A") for i in range(5)] + [_item(i + 5, f"ch-{i}") for i in range(25)]
    result = enforce_channel_caps(items, "videos")
    first_20 = result[:20]
    ch_a_count = sum(1 for r in first_20 if r["channel_id"] == "ch-A")
    assert ch_a_count <= 2


def test_reels_max_1_from_same_channel_in_first_10():
    # 3 ch-A items followed by 15 unique channel items (18 total)
    items = [_item(i, "ch-A") for i in range(3)] + [_item(i + 3, f"ch-{i}") for i in range(15)]
    result = enforce_channel_caps(items, "reels")
    first_10 = result[:10]
    ch_a_count = sum(1 for r in first_10 if r["channel_id"] == "ch-A")
    assert ch_a_count <= 1


def test_reels_max_2_from_same_channel_in_first_20():
    # 5 ch-A items followed by 30 unique channel items (35 total)
    items = [_item(i, "ch-A") for i in range(5)] + [_item(i + 5, f"ch-{i}") for i in range(30)]
    result = enforce_channel_caps(items, "reels")
    first_20 = result[:20]
    ch_a_count = sum(1 for r in first_20 if r["channel_id"] == "ch-A")
    assert ch_a_count <= 2


def test_articles_surface_returns_unchanged():
    items = [_item(i, "ch-A") for i in range(10)]
    result = enforce_channel_caps(items, "articles")
    assert result == items


def test_deferred_items_are_not_lost():
    items = [_item(i, "ch-A") for i in range(5)] + [_item(i + 5, f"ch-{i}") for i in range(25)]
    result = enforce_channel_caps(items, "videos")
    assert len(result) == len(items)
    result_ids = sorted(r["id"] for r in result)
    input_ids = sorted(r["id"] for r in items)
    assert result_ids == input_ids


def test_empty_list():
    assert enforce_channel_caps([], "videos") == []


def test_graceful_degradation_with_single_channel():
    items = [_item(i, "ch-A") for i in range(10)]
    result = enforce_channel_caps(items, "reels")
    assert len(result) == 10


def test_reels_best_effort_avoids_back_to_back_when_alternatives_exist():
    items = [
        _item(1, "ch-A"),
        _item(2, "ch-A"),
        _item(3, "ch-A"),
        _item(4, "ch-B"),
        _item(5, "ch-B"),
        _item(6, "ch-C"),
        _item(7, "ch-D"),
    ]

    result = enforce_channel_caps(items, "reels")

    for prev, current in zip(result, result[1:], strict=True):
        assert prev["channel_id"] != current["channel_id"]
