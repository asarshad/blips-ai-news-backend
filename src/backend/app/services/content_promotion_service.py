"""Event-driven promotion helpers for candidate content."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.services.promotion_service import PromotionService

logger = get_logger(__name__)

CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE = "content.promotion_eval.requested"


def should_queue_content_promotion(item: Any) -> bool:
    """Return True when a content row should be queued for promotion evaluation."""
    if getattr(item, "id", None) is None:
        return False
    if getattr(item, "type", None) not in (
        ContentType.ARTICLE,
        ContentType.VIDEO,
        ContentType.REEL,
    ):
        return False
    if getattr(item, "curation_status", None) != ContentStatus.CANDIDATE:
        return False
    if bool(getattr(item, "is_suppressed", False)):
        return False
    return True


def queue_content_promotion_request(
    db: Session,
    item: Any,
    *,
    now: Optional[datetime] = None,
) -> ContentEventOutbox | None:
    """Enqueue a durable promotion-evaluation request for one candidate item."""
    if not should_queue_content_promotion(item):
        return None

    existing = (
        db.query(ContentEventOutbox.id)
        .filter(
            ContentEventOutbox.content_item_id == int(item.id),
            ContentEventOutbox.event_type == CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE,
            ContentEventOutbox.status.in_(("pending", "processing")),
        )
        .first()
    )
    if existing is not None:
        return None

    event = ContentEventOutbox(
        content_item_id=int(item.id),
        event_type=CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE,
        payload={
            "content_id": int(item.id),
            "content_type": getattr(
                getattr(item, "type", None),
                "value",
                getattr(item, "type", None),
            ),
            "curation_status": getattr(
                getattr(item, "curation_status", None),
                "value",
                getattr(item, "curation_status", None),
            ),
        },
        status="pending",
        available_at=now or datetime.utcnow(),
    )
    db.add(event)
    return event


def process_content_promotion_request(
    db: Session,
    *,
    content_id: int,
) -> Dict[str, Any]:
    """Process one event-driven promotion request."""
    item = db.get(ContentItem, int(content_id))
    if item is None:
        logger.info("[content_promotion] skipping missing content_id=%s", content_id)
        return {
            "content_id": int(content_id),
            "changed": False,
            "skipped": True,
            "reason": "missing_content",
        }

    if item.type not in (ContentType.ARTICLE, ContentType.VIDEO, ContentType.REEL):
        logger.info(
            "[content_promotion] skipping unsupported type content_id=%s type=%s",
            item.id,
            item.type,
        )
        return {
            "content_id": int(item.id),
            "changed": False,
            "skipped": True,
            "reason": "unsupported_type",
            "content_type": item.type.value,
        }

    if item.curation_status != ContentStatus.CANDIDATE or item.is_suppressed:
        logger.info(
            "[content_promotion] skipping content_id=%s status=%s suppressed=%s",
            item.id,
            getattr(item.curation_status, "value", item.curation_status),
            bool(item.is_suppressed),
        )
        return {
            "content_id": int(item.id),
            "changed": False,
            "skipped": True,
            "reason": "not_candidate_or_suppressed",
            "content_type": item.type.value,
        }

    previous_status = item.curation_status
    previous_score = getattr(item, "promotion_score", None)

    result = PromotionService(db).run_promotion_job(content_types=(item.type,))
    db.flush()
    db.refresh(item)

    changed = item.curation_status != previous_status or getattr(item, "promotion_score", None) != previous_score
    logger.info(
        "[content_promotion] processed content_id=%s changed=%s promoted=%s score=%s",
        item.id,
        changed,
        item.curation_status == ContentStatus.PROMOTED,
        getattr(item, "promotion_score", None),
    )

    return {
        "content_id": int(item.id),
        "content_type": item.type.value,
        "changed": changed,
        "skipped": False,
        "promoted": item.curation_status == ContentStatus.PROMOTED,
        "promotion_score": getattr(item, "promotion_score", None),
        "promotion_reason": getattr(item, "promotion_reason", None),
        "readiness_status": (getattr(item, "readiness_status", None) or "").strip() or None,
        "result_promoted_ids": list(result.promoted_ids),
        "result_errors": list(result.errors),
    }
