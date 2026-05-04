"""Strategic content health checks for operator alerting.

These checks intentionally focus on app-level risk: no ready content for hours,
large stuck queues, and the major-news fast path failing to run or drain.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.content import ContentItem, ContentReadinessStatus, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.models.source_fetch_state import SourceFetchState
from app.services.major_news_constants import MAJOR_NEWS_DISCOVERED_VIA, MAJOR_NEWS_SOURCE_TYPE


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hours_since(now: datetime, value: datetime | None) -> float | None:
    dt = _as_utc(value)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 3600)


def _surface_value(content_type: ContentType) -> str:
    return {
        ContentType.ARTICLE: "articles",
        ContentType.VIDEO: "videos",
        ContentType.REEL: "reels",
    }[content_type]


def _count_ready_since(db: Session, *, content_type: ContentType, since: datetime) -> int:
    return int(
        db.query(func.count(ContentItem.id))
        .filter(
            ContentItem.type == content_type,
            ContentItem.curation_status == ContentStatus.PROMOTED,
            ContentItem.readiness_status == ContentReadinessStatus.READY.value,
            ContentItem.ready_at >= since.replace(tzinfo=None),
            ContentItem.is_suppressed.is_(False),
        )
        .scalar()
        or 0
    )


def _latest_ready_at(db: Session, *, content_type: ContentType | None = None) -> datetime | None:
    query = db.query(func.max(ContentItem.ready_at)).filter(
        ContentItem.curation_status == ContentStatus.PROMOTED,
        ContentItem.readiness_status == ContentReadinessStatus.READY.value,
        ContentItem.is_suppressed.is_(False),
    )
    if content_type is not None:
        query = query.filter(ContentItem.type == content_type)
    return query.scalar()


def _pending_reason_counts(rows: list[tuple[str | None, int]]) -> dict[str, int]:
    counts = Counter()
    for reason, count in rows:
        key = (reason or "unknown").strip() or "unknown"
        counts[key] += int(count or 0)
    return dict(counts.most_common(6))


def compute_strategic_content_health(
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return strategic content-health diagnostics and alert-worthy issues."""
    current = _as_utc(now) or datetime.now(timezone.utc)
    ready_stall_hours = max(1, int(settings.STRATEGIC_ALERT_READY_STALL_HOURS))
    surface_stall_hours = max(ready_stall_hours, int(settings.STRATEGIC_ALERT_SURFACE_STALL_HOURS))
    ready_since = current - timedelta(hours=ready_stall_hours)
    surface_since = current - timedelta(hours=surface_stall_hours)

    issues: list[dict[str, Any]] = []
    surfaces: dict[str, Any] = {}
    total_ready_recent = 0

    for content_type in (ContentType.ARTICLE, ContentType.VIDEO, ContentType.REEL):
        surface = _surface_value(content_type)
        ready_recent = _count_ready_since(db, content_type=content_type, since=ready_since)
        ready_surface_window = _count_ready_since(
            db,
            content_type=content_type,
            since=surface_since,
        )
        latest_ready = _latest_ready_at(db, content_type=content_type)
        total_ready_recent += ready_recent
        surfaces[surface] = {
            "ready_last_stall_window": ready_recent,
            "ready_last_surface_window": ready_surface_window,
            "latest_ready_at": latest_ready.isoformat() if latest_ready else None,
            "hours_since_latest_ready": (
                round(_hours_since(current, latest_ready), 2) if latest_ready else None
            ),
        }
        if ready_surface_window <= 0:
            issues.append(
                {
                    "key": f"{surface}_ready_stalled",
                    "severity": "warning",
                    "message": f"No ready {surface} produced in {surface_stall_hours}h",
                    "context": {
                        "surface": surface,
                        "window_hours": surface_stall_hours,
                        "latest_ready_at": latest_ready.isoformat() if latest_ready else "never",
                    },
                }
            )

    latest_any_ready = _latest_ready_at(db)
    if total_ready_recent <= 0:
        issues.append(
            {
                "key": "all_surfaces_ready_stalled",
                "severity": "critical",
                "message": f"No app-ready content produced on any surface in {ready_stall_hours}h",
                "context": {
                    "window_hours": ready_stall_hours,
                    "latest_ready_at": latest_any_ready.isoformat() if latest_any_ready else "never",
                },
            }
        )

    due_events = (
        db.query(
            ContentEventOutbox.event_type,
            func.count(ContentEventOutbox.id),
            func.min(ContentEventOutbox.available_at),
        )
        .filter(
            ContentEventOutbox.status.in_(("pending", "processing")),
            ContentEventOutbox.available_at <= current.replace(tzinfo=None),
        )
        .group_by(ContentEventOutbox.event_type)
        .all()
    )
    event_backlog_count = sum(int(row[1] or 0) for row in due_events)
    oldest_event_at = min((_as_utc(row[2]) for row in due_events if row[2] is not None), default=None)
    oldest_event_minutes = (
        round((current - oldest_event_at).total_seconds() / 60, 1) if oldest_event_at else None
    )
    event_backlog = {
        "due_count": event_backlog_count,
        "oldest_due_at": oldest_event_at.isoformat() if oldest_event_at else None,
        "oldest_due_minutes": oldest_event_minutes,
        "by_type": {str(row[0]): int(row[1] or 0) for row in due_events},
    }
    if (
        event_backlog_count >= int(settings.STRATEGIC_ALERT_EVENT_BACKLOG_COUNT)
        and oldest_event_minutes is not None
        and oldest_event_minutes >= int(settings.STRATEGIC_ALERT_EVENT_BACKLOG_MINUTES)
    ):
        issues.append(
            {
                "key": "content_event_backlog",
                "severity": "critical",
                "message": "Content event queue backlog is old and large",
                "context": event_backlog,
            }
        )

    pending_cutoff = current - timedelta(
        minutes=max(15, int(settings.STRATEGIC_ALERT_PROMOTED_PENDING_MINUTES))
    )
    pending_rows = (
        db.query(ContentItem.readiness_reason, func.count(ContentItem.id))
        .filter(
            ContentItem.curation_status == ContentStatus.PROMOTED,
            ContentItem.readiness_status == ContentReadinessStatus.PENDING.value,
            ContentItem.readiness_updated_at <= pending_cutoff.replace(tzinfo=None),
            ContentItem.is_suppressed.is_(False),
        )
        .group_by(ContentItem.readiness_reason)
        .all()
    )
    pending_total = sum(int(row[1] or 0) for row in pending_rows)
    promoted_pending = {
        "stuck_count": pending_total,
        "older_than_minutes": int(settings.STRATEGIC_ALERT_PROMOTED_PENDING_MINUTES),
        "by_reason": _pending_reason_counts(pending_rows),
    }
    if pending_total >= int(settings.STRATEGIC_ALERT_PROMOTED_PENDING_COUNT):
        issues.append(
            {
                "key": "promoted_pending_backlog",
                "severity": "warning",
                "message": "Large promoted-content pending backlog",
                "context": promoted_pending,
            }
        )

    probe_states = (
        db.query(SourceFetchState)
        .filter(SourceFetchState.source_type == MAJOR_NEWS_SOURCE_TYPE)
        .all()
    )
    latest_probe_at = max((_as_utc(row.updated_at) for row in probe_states), default=None)
    cooldown_states = [
        row
        for row in probe_states
        if row.cooldown_until is not None and _as_utc(row.cooldown_until) > current
    ]
    degraded_states = [
        row for row in probe_states if str(row.health_status or "").lower() != "healthy"
    ]
    no_insert_since = current - timedelta(hours=int(settings.STRATEGIC_ALERT_MAJOR_NEWS_NO_INSERT_HOURS))
    major_probe_insert_count = int(
        db.query(func.count(ContentItem.id))
        .filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.discovered_via == MAJOR_NEWS_DISCOVERED_VIA,
            ContentItem.created_at >= no_insert_since.replace(tzinfo=None),
            ContentItem.is_suppressed.is_(False),
        )
        .scalar()
        or 0
    )
    stuck_major = int(
        db.query(func.count(ContentItem.id))
        .filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.is_major_tech_news.is_(True),
            ContentItem.readiness_status == ContentReadinessStatus.PENDING.value,
            ContentItem.created_at
            <= (
                current
                - timedelta(minutes=int(settings.STRATEGIC_ALERT_MAJOR_NEWS_STUCK_MINUTES))
            ).replace(tzinfo=None),
            ContentItem.is_suppressed.is_(False),
        )
        .scalar()
        or 0
    )
    major_news = {
        "feed_count": len(probe_states),
        "latest_probe_at": latest_probe_at.isoformat() if latest_probe_at else None,
        "hours_since_latest_probe": (
            round(_hours_since(current, latest_probe_at), 2) if latest_probe_at else None
        ),
        "cooldown_feed_count": len(cooldown_states),
        "degraded_feed_count": len(degraded_states),
        "probe_insert_count_window_hours": int(settings.STRATEGIC_ALERT_MAJOR_NEWS_NO_INSERT_HOURS),
        "probe_insert_count": major_probe_insert_count,
        "stuck_confirmed_major_count": stuck_major,
    }
    if latest_probe_at is None or (
        _hours_since(current, latest_probe_at) or 0
    ) >= int(settings.STRATEGIC_ALERT_MAJOR_NEWS_PROBE_STALE_HOURS):
        issues.append(
            {
                "key": "major_news_probe_stale",
                "severity": "warning",
                "message": "Major-news probe has not reported recently",
                "context": major_news,
            }
        )
    if probe_states and len(cooldown_states) == len(probe_states):
        issues.append(
            {
                "key": "major_news_all_feeds_cooling_down",
                "severity": "warning",
                "message": "All major-news feeds are currently cooling down",
                "context": major_news,
            }
        )
    # Only alert on no-inserts when there are also stuck confirmed majors — otherwise
    # it's indistinguishable from "no major news happened overnight", which is normal.
    if major_probe_insert_count <= 0 and stuck_major >= int(settings.STRATEGIC_ALERT_MAJOR_NEWS_STUCK_MIN_COUNT):
        issues.append(
            {
                "key": "major_news_no_recent_inserts",
                "severity": "warning",
                "message": "Major-news probe inserted no articles in its monitoring window",
                "context": major_news,
            }
        )
    if stuck_major >= int(settings.STRATEGIC_ALERT_MAJOR_NEWS_STUCK_MIN_COUNT):
        issues.append(
            {
                "key": "major_news_stuck_pending",
                "severity": "warning",
                "message": "Confirmed major-news articles are stuck before app readiness",
                "context": major_news,
            }
        )

    severity_rank = {"healthy": 0, "warning": 1, "critical": 2}
    status = "healthy"
    for issue in issues:
        severity = str(issue.get("severity") or "warning")
        if severity_rank.get(severity, 1) > severity_rank[status]:
            status = severity

    return {
        "as_of": current.isoformat(),
        "status": status,
        "issues": issues,
        "surfaces": surfaces,
        "event_backlog": event_backlog,
        "promoted_pending": promoted_pending,
        "major_news": major_news,
        "thresholds": {
            "ready_stall_hours": ready_stall_hours,
            "surface_stall_hours": surface_stall_hours,
            "event_backlog_count": int(settings.STRATEGIC_ALERT_EVENT_BACKLOG_COUNT),
            "event_backlog_minutes": int(settings.STRATEGIC_ALERT_EVENT_BACKLOG_MINUTES),
            "promoted_pending_count": int(settings.STRATEGIC_ALERT_PROMOTED_PENDING_COUNT),
            "promoted_pending_minutes": int(settings.STRATEGIC_ALERT_PROMOTED_PENDING_MINUTES),
        },
    }


def emit_strategic_content_alerts(health: dict[str, Any]) -> int:
    """Emit deduped alerts for strategic issues and return sent count."""
    if not bool(getattr(settings, "STRATEGIC_CONTENT_ALERTS_ENABLED", True)):
        return 0

    from app.services.alerting_service import AlertSeverity, alert_strategic_content_issue

    sent = 0
    for issue in health.get("issues", []):
        severity_value = str(issue.get("severity") or "warning").lower()
        severity = (
            AlertSeverity.CRITICAL
            if severity_value == AlertSeverity.CRITICAL.value
            else AlertSeverity.WARNING
        )
        if alert_strategic_content_issue(
            issue_key=str(issue.get("key") or "unknown"),
            message=str(issue.get("message") or "Strategic content issue"),
            severity=severity,
            context=issue.get("context") if isinstance(issue.get("context"), dict) else {},
        ):
            sent += 1
    return sent
