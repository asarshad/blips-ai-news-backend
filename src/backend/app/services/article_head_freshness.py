"""Helpers for keeping feed ordering biased toward fresher inventory."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Sequence, TypeVar
from zoneinfo import ZoneInfo

ARTICLE_FRESH_HEAD_SIZE = 10
FEED_DAY_TIMEZONE = ZoneInfo("America/Vancouver")

T = TypeVar("T")


def prioritize_recent_head(
    items: Sequence[T],
    *,
    head_size: int = ARTICLE_FRESH_HEAD_SIZE,
    now: datetime | None = None,
) -> list[T]:
    """Sort the feed by published day, then by score within each day.

    Day ordering is the primary freshness signal across the entire feed:
    - newer UTC published days first
    - within the same day, higher promotion/global score first
    - undated items keep their existing relative order at the end

    ``head_size`` is retained for backward compatibility with older callers,
    but the ordering now applies to the full list instead of only a front slice.
    """
    if head_size <= 0 or len(items) <= 1:
        return list(items)

    indexed_items = list(enumerate(items))
    dated_items: list[tuple[int, T, datetime]] = []
    undated_items: list[tuple[int, T]] = []

    for index, item in indexed_items:
        published_at = _coerce_datetime(_published_at(item))
        if published_at is None:
            undated_items.append((index, item))
            continue
        dated_items.append((index, item, published_at.astimezone(FEED_DAY_TIMEZONE)))

    ordered_dated_items = sorted(
        dated_items,
        key=lambda entry: (
            -_date_sort_value(entry[2].date()),
            -_numeric_sort_value(_promotion_score(entry[1])),
            -_numeric_sort_value(_global_score(entry[1])),
            -entry[2].timestamp(),
            entry[0],
        ),
    )

    return [
        *(item for _, item, _ in ordered_dated_items),
        *(item for _, item in undated_items),
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
    published_day = published_at.astimezone(FEED_DAY_TIMEZONE).date()
    if published_day == today:
        return 0
    if published_day == yesterday:
        return 1
    return None


def _date_sort_value(value: date) -> int:
    return value.toordinal()


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
