"""Article supply metrics for operator visibility."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.content import ContentItem, ContentReadinessStatus, ContentStatus, ContentType
from app.models.ingestion_progress import IngestionProgress


def compute_article_supply_metrics(db: Session, *, days: int = 14) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    window_days = max(1, int(days))
    start_day = now.date() - timedelta(days=window_days - 1)
    start_dt = datetime.combine(start_day, time.min)

    day_rows = {
        (start_day + timedelta(days=offset)).isoformat(): {
            "day": (start_day + timedelta(days=offset)).isoformat(),
            "total_published": 0,
            "candidate_count": 0,
            "promoted_count": 0,
            "ready_count": 0,
            "pending_count": 0,
            "pending_by_reason": {},
            "major_news_probe_count": 0,
            "major_news_confirmed_count": 0,
        }
        for offset in range(window_days)
    }

    source_rows: dict[tuple[str, str], dict[str, Any]] = {}

    items = (
        db.query(ContentItem)
        .filter(
            ContentItem.type == ContentType.ARTICLE,
            ContentItem.published_at >= start_dt,
            ContentItem.is_suppressed.is_(False),
        )
        .all()
    )

    for item in items:
        published_at = getattr(item, "published_at", None)
        if published_at is None:
            continue
        day_key = published_at.date().isoformat()
        if day_key not in day_rows:
            continue

        row = day_rows[day_key]
        row["total_published"] += 1
        source_key = ((getattr(item, "source", None) or "unknown").strip() or "unknown", day_key)
        source_row = source_rows.setdefault(
            source_key,
            {
                "source": source_key[0],
                "day": day_key,
                "attempted": 0,
                "inserted": 0,
                "candidate": 0,
                "promoted": 0,
                "ready": 0,
                "pending": 0,
                "pending_by_reason": {},
                "major_news_probe": 0,
                "major_news_confirmed": 0,
                "status": "healthy",
                "ingestion_status": None,
            },
        )
        if (getattr(item, "discovered_via", None) or "") == "major_news_probe":
            row["major_news_probe_count"] += 1
            source_row["major_news_probe"] += 1
        if getattr(item, "is_major_tech_news", None) is True:
            row["major_news_confirmed_count"] += 1
            source_row["major_news_confirmed"] += 1

        if item.curation_status == ContentStatus.CANDIDATE:
            row["candidate_count"] += 1
            source_row["candidate"] += 1
            continue

        row["promoted_count"] += 1
        source_row["promoted"] += 1
        if (getattr(item, "readiness_status", None) or "").strip() == ContentReadinessStatus.READY.value:
            row["ready_count"] += 1
            source_row["ready"] += 1
        else:
            row["pending_count"] += 1
            source_row["pending"] += 1
            reason = (getattr(item, "readiness_reason", None) or "unknown").strip() or "unknown"
            row_reason_counts = Counter(row["pending_by_reason"])
            row_reason_counts[reason] += 1
            row["pending_by_reason"] = dict(sorted(row_reason_counts.items()))
            source_reason_counts = Counter(source_row["pending_by_reason"])
            source_reason_counts[reason] += 1
            source_row["pending_by_reason"] = dict(sorted(source_reason_counts.items()))

    progress_rows = (
        db.query(IngestionProgress)
        .filter(
            IngestionProgress.day_utc >= start_day,
            IngestionProgress.source_type == "rss",
        )
        .all()
    )

    for progress in progress_rows:
        day_key = progress.day_utc.isoformat()
        key = (str(progress.feed_name), day_key)
        row = source_rows.setdefault(
            key,
            {
                "source": str(progress.feed_name),
                "day": day_key,
                "attempted": 0,
                "inserted": 0,
                "candidate": 0,
                "promoted": 0,
                "ready": 0,
                "pending": 0,
                "pending_by_reason": {},
                "major_news_probe": 0,
                "major_news_confirmed": 0,
                "status": "healthy",
                "ingestion_status": None,
            },
        )
        row["attempted"] = int(progress.items_attempted or 0)
        row["inserted"] = int(progress.items_ingested or 0)
        row["ingestion_status"] = progress.status
        degraded = progress.status == "failed" or int(progress.retry_count or 0) >= int(
            settings.ARTICLE_RSS_DEGRADED_RETRY_COUNT_THRESHOLD
        )
        row["status"] = "degraded" if degraded else "healthy"

    return {
        "as_of": now.isoformat(),
        "days": window_days,
        "daily": [day_rows[key] for key in sorted(day_rows)],
        "sources": sorted(
            source_rows.values(),
            key=lambda row: (row["day"], row["source"]),
        ),
    }
