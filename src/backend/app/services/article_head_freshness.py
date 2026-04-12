"""Helpers for keeping feed heads biased toward fresher inventory."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence, TypeVar

ARTICLE_FRESH_HEAD_SIZE = 10
ARTICLE_RECENT_HEAD_CANDIDATE_LIMIT = 50

T = TypeVar("T")


def prioritize_recent_head(
    items: Sequence[T],
    *,
    head_size: int = ARTICLE_FRESH_HEAD_SIZE,
    now: datetime | None = None,
) -> list[T]:
    """Build a recent feed head from today and yesterday first.

    The top feed slots should feel current and shared across devices:
    - today bucket first
    - then yesterday bucket
    - then the existing feed order as fallback when recent inventory is thin
    """
    if head_size <= 0 or len(items) <= 1:
        return list(items)

    indexed_items = list(enumerate(items))
    effective_head_size = min(head_size, len(indexed_items))
    reference = (now or _utcnow()).astimezone(timezone.utc).date()
    yesterday = reference - timedelta(days=1)

    recent_candidates = sorted(
        (
            pair
            for pair in indexed_items
            if _published_day_bucket(pair[1], today=reference, yesterday=yesterday) is not None
        ),
        key=lambda pair: (
            _published_day_bucket(pair[1], today=reference, yesterday=yesterday),
            -_numeric_sort_value(_promotion_score(pair[1])),
            -_numeric_sort_value(_global_score(pair[1])),
            -_datetime_sort_value(_published_at(pair[1])),
            pair[0],
        ),
    )
    head_selection = recent_candidates[:effective_head_size]
    selected_indexes = {index for index, _ in head_selection}

    if len(head_selection) < effective_head_size:
        for pair in indexed_items:
            index, _item = pair
            if index in selected_indexes:
                continue
            head_selection.append(pair)
            selected_indexes.add(index)
            if len(head_selection) >= effective_head_size:
                break

    return [
        *(item for _, item in head_selection),
        *(item for index, item in indexed_items if index not in selected_indexes),
    ]


def prioritize_article_head(
    items: Sequence[T],
    *,
    head_size: int = ARTICLE_FRESH_HEAD_SIZE,
    now: datetime | None = None,
) -> list[T]:
    """Backward-compatible alias for article callers."""
    return prioritize_recent_head(items, head_size=head_size, now=now)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _published_at(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("published_at")
    return getattr(item, "published_at", None)


def _created_at(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("created_at")
    return getattr(item, "created_at", None)


def _promotion_score(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("promotion_score")
    return getattr(item, "promotion_score", None)


def _global_score(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("global_score")
    return getattr(item, "global_score", None)


def _published_day_bucket(
    item: Any,
    *,
    today,
    yesterday,
) -> int | None:
    published_at = _coerce_datetime(_published_at(item))
    if published_at is None:
        return None
    published_day = published_at.astimezone(timezone.utc).date()
    if published_day == today:
        return 0
    if published_day == yesterday:
        return 1
    return None


def _numeric_sort_value(value: Any) -> float:
    if value is None:
        return float("-inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


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
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None
