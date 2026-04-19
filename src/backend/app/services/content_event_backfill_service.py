"""Operator helpers for backfilling outbox events for existing content."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.content import ContentItem, ContentReadinessStatus, ContentType
from app.services.article_image_service import queue_article_image_verification_request
from app.services.content_ai_service import queue_content_ai_summary_request
from app.services.content_promotion_service import queue_content_promotion_request
from app.services.content_readiness import sync_content_readiness

logger = get_logger(__name__)


def enqueue_pending_content_events(
    db: Session,
    *,
    lookback_days: int = 7,
    limit: int = 500,
    pending_only: bool = True,
) -> dict[str, Any]:
    """Backfill outbox events for existing content rows that still need work."""
    cutoff = datetime.utcnow() - timedelta(days=max(1, int(lookback_days)))
    query = db.query(ContentItem).filter(ContentItem.published_at >= cutoff)
    if pending_only:
        query = query.filter(ContentItem.readiness_status != ContentReadinessStatus.READY.value)

    items = (
        query.order_by(ContentItem.published_at.desc(), ContentItem.id.desc())
        .limit(max(1, int(limit)))
        .all()
    )

    scanned = 0
    readiness_updates = 0
    promotion_events = 0
    ai_events = 0
    image_events = 0
    by_type: dict[str, int] = {}
    pending_changes = 0

    for item in items:
        scanned += 1
        type_key = getattr(item.type, "value", str(item.type))
        by_type[type_key] = by_type.get(type_key, 0) + 1

        previous_status = (getattr(item, "readiness_status", None) or "").strip()
        previous_reason = (getattr(item, "readiness_reason", None) or "").strip()

        sync_content_readiness(db, item)

        current_status = (getattr(item, "readiness_status", None) or "").strip()
        current_reason = (getattr(item, "readiness_reason", None) or "").strip()
        if current_status != previous_status or current_reason != previous_reason:
            readiness_updates += 1

        if queue_content_promotion_request(db, item) is not None:
            promotion_events += 1
        if queue_content_ai_summary_request(db, item) is not None:
            ai_events += 1
        if item.type == ContentType.ARTICLE:
            if queue_article_image_verification_request(db, item) is not None:
                image_events += 1

        pending_changes += 1
        if pending_changes >= 100:
            db.commit()
            pending_changes = 0

    if pending_changes:
        db.commit()

    result = {
        "job": "content_event_backfill",
        "lookback_days": max(1, int(lookback_days)),
        "limit": max(1, int(limit)),
        "pending_only": bool(pending_only),
        "scanned": scanned,
        "readiness_updates": readiness_updates,
        "queued": {
            "promotion": promotion_events,
            "ai_summary": ai_events,
            "article_image_verification": image_events,
        },
        "by_type": by_type,
    }
    logger.info("[content_event_backfill] %s", result)
    return result
