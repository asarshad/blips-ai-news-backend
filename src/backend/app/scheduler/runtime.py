"""Thread-safe runtime state for worker scheduler coordination.

Tracks whether heavy jobs are currently active and when inline follow-up phases
last completed, so scheduled safety-net jobs can avoid bunching on top of the
main ingestion cycle.
"""

from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict

FETCH_NEWS_JOB = "fetch_news"
FETCH_NEWS_INLINE_AI_RETRY = "fetch_news.inline_ai_retry"
FETCH_NEWS_INLINE_CLUSTERING = "fetch_news.inline_clustering"
FETCH_NEWS_INLINE_PROMOTION = "fetch_news.inline_promotion"
AI_RETRY_JOB = "ai_retry"
CLUSTERING_JOB = "clustering"
PROMOTION_JOB = "promotion"

_LOCK = threading.Lock()


@dataclass
class JobState:
    active_count: int = 0
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_success_at: str | None = None
    last_duration_seconds: float | None = None


_STATE: Dict[str, JobState] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _state_for(job_name: str) -> JobState:
    state = _STATE.get(job_name)
    if state is None:
        state = JobState()
        _STATE[job_name] = state
    return state


def mark_job_started(job_name: str) -> datetime:
    started_at = _utcnow()
    with _LOCK:
        state = _state_for(job_name)
        state.active_count += 1
        state.last_started_at = started_at.isoformat()
    return started_at


def mark_job_finished(job_name: str, started_at: datetime, *, success: bool) -> None:
    finished_at = _utcnow()
    duration_seconds = max(0.0, (finished_at - started_at).total_seconds())
    with _LOCK:
        state = _state_for(job_name)
        state.active_count = max(0, state.active_count - 1)
        state.last_finished_at = finished_at.isoformat()
        state.last_duration_seconds = round(duration_seconds, 3)
        if success:
            state.last_success_at = finished_at.isoformat()


def is_job_active(job_name: str) -> bool:
    with _LOCK:
        return _state_for(job_name).active_count > 0


def succeeded_within(job_name: str, *, within_seconds: int) -> bool:
    with _LOCK:
        last_success_at = _state_for(job_name).last_success_at
    if not last_success_at:
        return False
    try:
        last_success = datetime.fromisoformat(last_success_at)
    except ValueError:
        return False
    return (_utcnow() - last_success).total_seconds() < max(0, within_seconds)


def get_job_state(job_name: str) -> Dict[str, Any]:
    with _LOCK:
        return asdict(_state_for(job_name))


def get_followup_cooldown_seconds() -> int:
    raw = os.getenv("FOLLOWUP_JOB_COOLDOWN_SECONDS", "900")
    try:
        return max(60, int(raw))
    except ValueError:
        return 900


def log_memory_snapshot(logger, label: str) -> None:
    """Emit a best-effort process memory snapshot for heavy worker phases."""
    try:
        from app.core.observability import get_process_runtime_stats

        stats = get_process_runtime_stats()
        logger.info(
            "[%s] process_memory rss_mb=%s peak_rss_mb=%s uptime_seconds=%s",
            label,
            stats.get("rss_mb"),
            stats.get("peak_rss_mb"),
            stats.get("uptime_seconds"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to capture process memory snapshot for %s: %s", label, exc)


def reset_job_runtime_state() -> None:
    with _LOCK:
        _STATE.clear()
