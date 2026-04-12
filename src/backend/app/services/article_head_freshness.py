"""Helpers for keeping the article head biased toward fresher inventory."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence, TypeVar

ARTICLE_FRESH_HEAD_SIZE = 10

T = TypeVar("T")


def prioritize_article_head(
    items: Sequence[T],
    *,
    head_size: int = ARTICLE_FRESH_HEAD_SIZE,
) -> list[T]:
    """Promote fresher article tiers into the visible head.

    The article feed intentionally allows older Tier B/C items deeper in the
    session snapshot, but the first visible slots should prefer Tier A, then
    Tier B, before letting evergreen content surface.
    """
    if head_size <= 0 or len(items) <= 1:
        return list(items)

    indexed_items = list(enumerate(items))
    effective_head_size = min(head_size, len(indexed_items))

    prioritized = sorted(
        indexed_items,
        key=lambda pair: (
            _freshness_rank(pair[1]),
            -_datetime_sort_value(_published_at(pair[1])),
            -_datetime_sort_value(_created_at(pair[1])),
            pair[0],
        ),
    )
    head_selection = prioritized[:effective_head_size]
    selected_indexes = {index for index, _ in head_selection}

    return [
        *(item for _, item in head_selection),
        *(item for index, item in indexed_items if index not in selected_indexes),
    ]


def _freshness_rank(item: Any) -> int:
    if isinstance(item, dict):
        tier = item.get("freshness_tier")
    else:
        tier = getattr(item, "freshness_tier", None)

    return {
        "A": 0,
        "B": 1,
        "C": 2,
    }.get(str(tier).upper(), 3)


def _published_at(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("published_at")
    return getattr(item, "published_at", None)


def _created_at(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("created_at")
    return getattr(item, "created_at", None)


def _datetime_sort_value(value: Any) -> float:
    parsed = _coerce_datetime(value)
    if parsed is None:
        return float("-inf")
    return parsed.timestamp()


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None
