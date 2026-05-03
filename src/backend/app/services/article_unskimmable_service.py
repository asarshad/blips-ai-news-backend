"""Terminal handling for articles that never produced usable text."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.ingestion_budget import IngestionBudget
from app.services.content_readiness import sync_content_readiness

logger = get_logger(__name__)

ARTICLE_UNSKIMMABLE_TERMINAL_REASON = "article_unskimmable_terminal"


def is_terminal_unskimmable_article(item: Any) -> bool:
    """Return True when an article has already been abandoned as unskimmable."""
    if getattr(item, "type", None) != ContentType.ARTICLE:
        return False
    marker = str(getattr(item, "tech_relevance_reason", "") or "")
    return (
        bool(getattr(item, "is_suppressed", False))
        and getattr(item, "curation_status", None) == ContentStatus.CANDIDATE
        and marker.startswith(f"{ARTICLE_UNSKIMMABLE_TERMINAL_REASON}:")
    )


def reject_terminal_unskimmable_article(
    db: Session,
    item: ContentItem,
    *,
    reason: str,
    now: datetime | None = None,
) -> bool:
    """Remove a permanently unskimmable article from usable supply.

    The item is preserved for audit/dedupe, but it is no longer promoted,
    visible, retryable, or counted against the article ingestion budget.
    """
    if item.type != ContentType.ARTICLE:
        return False
    if is_terminal_unskimmable_article(item):
        return False

    ts = now or datetime.utcnow()
    compact_reason = " ".join((reason or "no_extractable_text").strip().split())
    marker = f"{ARTICLE_UNSKIMMABLE_TERMINAL_REASON}:{compact_reason}"[:255]

    item.curation_status = ContentStatus.CANDIDATE
    item.is_suppressed = True
    item.ai_processed = True
    item.summary = None
    item.tech_relevance_reason = marker
    item.promotion_reason = "Rejected automatically: no extractable article text"
    item.last_modified_by = "system:article_unskimmable"
    item.last_modified_at = ts
    item.updated_at = ts
    sync_content_readiness(db, item, now=ts)

    released = _release_article_budget_slot(db, item)
    logger.info(
        "[article_unskimmable] terminal reject content_id=%s reason=%s budget_slot_released=%s",
        item.id,
        compact_reason,
        released,
    )
    return True


def _release_article_budget_slot(db: Session, item: ContentItem) -> bool:
    day = getattr(item, "ingestion_day", None)
    if day is None:
        created_at = getattr(item, "created_at", None)
        if isinstance(created_at, datetime):
            day = created_at.date()
    if day is None:
        return False

    budget = (
        db.query(IngestionBudget)
        .filter(
            IngestionBudget.day == day,
            IngestionBudget.content_type == ContentType.ARTICLE,
        )
        .with_for_update()
        .one_or_none()
    )
    if budget is None:
        return False

    before = int(budget.inserted or 0)
    if before <= 0:
        return False
    budget.inserted = before - 1
    return True
