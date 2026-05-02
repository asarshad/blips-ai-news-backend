"""Normalize external fetch responses into ingestion actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Mapping, Optional

ACTION_SUCCESS = "success"
ACTION_CANONICAL_REDIRECT = "canonical_redirect"
ACTION_RATE_LIMITED = "rate_limited"
ACTION_BLOCKED = "blocked"
ACTION_GONE = "gone"
ACTION_TRANSIENT_ERROR = "transient_error"
ACTION_CLIENT_ERROR = "client_error"
ACTION_EMPTY = "empty"

STATUS_HEALTHY = "healthy"
STATUS_COOLDOWN = "cooldown"
STATUS_DEGRADED = "degraded"
STATUS_DISABLED = "disabled"


@dataclass(frozen=True)
class FetchOutcome:
    action: str
    status_code: Optional[int] = None
    error: Optional[str] = None
    retry_after_seconds: Optional[int] = None
    canonical_url: Optional[str] = None
    redirect_count: int = 0

    @property
    def succeeded(self) -> bool:
        return self.action in {ACTION_SUCCESS, ACTION_CANONICAL_REDIRECT}

    @property
    def should_retry_source_later(self) -> bool:
        return self.action in {ACTION_RATE_LIMITED, ACTION_TRANSIENT_ERROR}

    @property
    def should_pause_for_day(self) -> bool:
        return self.action in {ACTION_BLOCKED, ACTION_GONE}


def parse_retry_after(value: Optional[str], *, now: Optional[datetime] = None) -> Optional[int]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is not None:
            offset = dt.utcoffset()
            if offset is not None:
                dt = dt.replace(tzinfo=None) - offset
        current = now or datetime.utcnow()
        return max(0, int((dt - current).total_seconds()))
    except Exception:
        return None


def classify_http_response(
    *,
    status_code: int,
    headers: Optional[Mapping[str, str]] = None,
    original_url: Optional[str] = None,
    final_url: Optional[str] = None,
    redirect_statuses: Optional[list[int]] = None,
    error: Optional[str] = None,
) -> FetchOutcome:
    headers = headers or {}
    redirect_statuses = redirect_statuses or []
    redirect_count = len(redirect_statuses)
    permanent_redirect = any(status in {301, 308} for status in redirect_statuses)
    canonical_url = (
        final_url if permanent_redirect and final_url and final_url != original_url else None
    )

    if status_code == 429:
        return FetchOutcome(
            action=ACTION_RATE_LIMITED,
            status_code=status_code,
            error=error or "HTTP 429",
            retry_after_seconds=parse_retry_after(headers.get("Retry-After")),
            canonical_url=canonical_url,
            redirect_count=redirect_count,
        )
    if status_code in {401, 403}:
        return FetchOutcome(
            action=ACTION_BLOCKED,
            status_code=status_code,
            error=error or f"HTTP {status_code}",
            canonical_url=canonical_url,
            redirect_count=redirect_count,
        )
    if status_code in {404, 410}:
        return FetchOutcome(
            action=ACTION_GONE,
            status_code=status_code,
            error=error or f"HTTP {status_code}",
            canonical_url=canonical_url,
            redirect_count=redirect_count,
        )
    if status_code in {408, 425} or 500 <= status_code <= 599:
        return FetchOutcome(
            action=ACTION_TRANSIENT_ERROR,
            status_code=status_code,
            error=error or f"HTTP {status_code}",
            canonical_url=canonical_url,
            redirect_count=redirect_count,
        )
    if status_code >= 400:
        return FetchOutcome(
            action=ACTION_CLIENT_ERROR,
            status_code=status_code,
            error=error or f"HTTP {status_code}",
            canonical_url=canonical_url,
            redirect_count=redirect_count,
        )
    if canonical_url:
        return FetchOutcome(
            action=ACTION_CANONICAL_REDIRECT,
            status_code=status_code,
            canonical_url=canonical_url,
            redirect_count=redirect_count,
        )
    return FetchOutcome(
        action=ACTION_SUCCESS,
        status_code=status_code,
        canonical_url=canonical_url,
        redirect_count=redirect_count,
    )


def classify_exception(exc: BaseException) -> FetchOutcome:
    text = str(exc)
    return FetchOutcome(action=ACTION_TRANSIENT_ERROR, error=text or exc.__class__.__name__)


def cooldown_until_for_outcome(
    outcome: FetchOutcome,
    *,
    now: Optional[datetime] = None,
    consecutive_failures: int = 0,
) -> Optional[datetime]:
    current = now or datetime.utcnow()
    if outcome.action == ACTION_RATE_LIMITED:
        retry_after = outcome.retry_after_seconds
        if retry_after is None:
            retry_after = 30 * 60
        return current + timedelta(seconds=max(60, min(int(retry_after), 6 * 60 * 60)))
    if outcome.action == ACTION_TRANSIENT_ERROR:
        minutes = min(60, 5 * max(1, int(consecutive_failures or 1)))
        return current + timedelta(minutes=minutes)
    if outcome.action == ACTION_BLOCKED:
        return current + timedelta(hours=6)
    if outcome.action == ACTION_GONE:
        return current + timedelta(days=1)
    return None


def health_status_for_outcome(outcome: FetchOutcome) -> str:
    if outcome.succeeded:
        return STATUS_HEALTHY
    if outcome.action in {ACTION_RATE_LIMITED, ACTION_TRANSIENT_ERROR}:
        return STATUS_COOLDOWN
    if outcome.action in {ACTION_BLOCKED, ACTION_CLIENT_ERROR, ACTION_EMPTY}:
        return STATUS_DEGRADED
    if outcome.action == ACTION_GONE:
        return STATUS_DISABLED
    return STATUS_DEGRADED
