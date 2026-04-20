"""Shared retry-state sentinels for AI processing backlogs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.config import settings

ARTICLE_RETRY_SENTINEL_PREFIX = "__blips_article_retry__"
VIDEO_SUMMARY_RETRY_SENTINEL_PREFIX = "__blips_video_summary_retry__"


def is_article_retry_summary(summary: Any) -> bool:
    """Return True when an article summary stores retry metadata, not copy."""
    return _trimmed(summary).startswith(f"{ARTICLE_RETRY_SENTINEL_PREFIX}:")


def is_video_summary_retry_summary(summary: Any) -> bool:
    """Return True when a video summary stores retry metadata, not copy."""
    return _trimmed(summary).startswith(f"{VIDEO_SUMMARY_RETRY_SENTINEL_PREFIX}:")


def article_retry_state(item: Any, *, now: datetime | None = None) -> dict[str, object]:
    """Return bounded retry state for an unskimmable recent article."""
    return _retry_state(
        getattr(item, "summary", None),
        prefix=ARTICLE_RETRY_SENTINEL_PREFIX,
        max_attempts=int(settings.ARTICLE_UNSKIMMABLE_RETRY_MAX_ATTEMPTS),
        window=timedelta(hours=int(settings.ARTICLE_UNSKIMMABLE_RETRY_WINDOW_HOURS)),
        now=now,
    )


def record_article_retry_deferral(item: Any, *, now: datetime | None = None) -> int:
    """Store article retry metadata and return the new attempt count."""
    now_utc = now or datetime.utcnow()
    state = article_retry_state(item, now=now_utc)
    first_failed_at, attempts = _next_retry_window(
        state,
        window=timedelta(hours=int(settings.ARTICLE_UNSKIMMABLE_RETRY_WINDOW_HOURS)),
        now=now_utc,
    )
    item.summary = f"{ARTICLE_RETRY_SENTINEL_PREFIX}:v1:{attempts}:{first_failed_at.isoformat()}"
    item.updated_at = now_utc
    return attempts


def video_summary_retry_state(item: Any, *, now: datetime | None = None) -> dict[str, object]:
    """Return bounded retry state for a video that produced no usable summary."""
    return _retry_state(
        getattr(item, "summary", None),
        prefix=VIDEO_SUMMARY_RETRY_SENTINEL_PREFIX,
        max_attempts=int(settings.VIDEO_SUMMARY_RETRY_MAX_ATTEMPTS),
        window=timedelta(hours=int(settings.VIDEO_SUMMARY_RETRY_WINDOW_HOURS)),
        now=now,
    )


def record_video_summary_failure(
    item: Any,
    *,
    reason: str,
    now: datetime | None = None,
) -> dict[str, object]:
    """Store video summary failure metadata and terminal-mark exhausted items."""
    now_utc = now or datetime.utcnow()
    state = video_summary_retry_state(item, now=now_utc)
    first_failed_at, attempts = _next_retry_window(
        state,
        window=timedelta(hours=int(settings.VIDEO_SUMMARY_RETRY_WINDOW_HOURS)),
        now=now_utc,
    )
    terminal = attempts >= int(settings.VIDEO_SUMMARY_RETRY_MAX_ATTEMPTS)

    item.summary = (
        f"{VIDEO_SUMMARY_RETRY_SENTINEL_PREFIX}:v1:{attempts}:{first_failed_at.isoformat()}"
    )
    item.ai_processed = bool(terminal)
    item.tech_relevance_reason = f"video_summary_failure:{_compact_reason(reason)}"[:255]
    item.updated_at = now_utc

    return {
        "attempts": attempts,
        "first_failed_at": first_failed_at,
        "terminal": terminal,
        "reason": reason,
    }


def _retry_state(
    summary: Any,
    *,
    prefix: str,
    max_attempts: int,
    window: timedelta,
    now: datetime | None = None,
) -> dict[str, object]:
    now_utc = now or datetime.utcnow()
    attempts = 0
    first_failed_at = None
    raw_summary = _trimmed(summary)
    if raw_summary.startswith(f"{prefix}:"):
        parts = raw_summary.split(":", 3)
        if len(parts) >= 4:
            try:
                attempts = max(0, int(parts[2]))
            except (TypeError, ValueError):
                attempts = 0
            try:
                first_failed_at = datetime.fromisoformat(parts[3])
            except ValueError:
                first_failed_at = None

    if first_failed_at is None:
        return {"attempts": attempts, "first_failed_at": None, "eligible": True}

    within_window = first_failed_at + window > now_utc
    eligible = not within_window or attempts < max_attempts
    return {
        "attempts": attempts,
        "first_failed_at": first_failed_at,
        "eligible": eligible,
    }


def _next_retry_window(
    state: dict[str, object],
    *,
    window: timedelta,
    now: datetime,
) -> tuple[datetime, int]:
    first_failed_at = state["first_failed_at"]
    attempts = int(state["attempts"])
    if not isinstance(first_failed_at, datetime) or first_failed_at + window <= now:
        first_failed_at = now
        attempts = 0
    return first_failed_at, attempts + 1


def _compact_reason(reason: str) -> str:
    normalized = " ".join((reason or "unknown").strip().split())
    return normalized.replace(":", "_")[:220] or "unknown"


def _trimmed(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
