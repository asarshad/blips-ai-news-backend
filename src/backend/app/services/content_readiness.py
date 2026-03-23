"""Shared readiness contract for feed-visible content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentReadinessStatus, ContentStatus, ContentType
from app.models.content_event import ContentEventOutbox
from app.video_surface_rules import (
    effective_content_type,
    surface_content_filter,
    visible_promotion_filter,
)

CONTENT_READY_EVENT_TYPE = "content.ready"
_READINESS_REASON_DESCRIPTIONS = {
    "suppressed": "Suppressed items are hidden from all client delivery paths.",
    "awaiting_promotion": "Promote this item before it can be delivered to users.",
    "blocked_promotion": "This item is blocked by promotion policy and is not client-visible.",
    "missing_article_source": "This article is missing a canonical source URL.",
    "awaiting_ai_processing": "This article still needs AI summarization before delivery.",
    "missing_article_summary": "This article does not yet have a usable summary.",
    "missing_video_title": "This video is missing a title.",
    "missing_video_url": "This video is missing a playable URL.",
    "article_ready": "This article is ready for client delivery.",
    "video_ready": "This video is ready for client delivery.",
    "reel_ready": "This reel is ready for client delivery.",
}


@dataclass(frozen=True)
class ContentReadinessDecision:
    """Computed readiness state for a content item."""

    status: ContentReadinessStatus
    reason: str
    effective_type: ContentType
    surfaces: tuple[str, ...]


@dataclass(frozen=True)
class ContentReadinessSyncResult:
    """Result from synchronizing persisted readiness fields."""

    previous_status: ContentReadinessStatus
    current_status: ContentReadinessStatus
    reason: str
    transitioned_to_ready: bool
    surfaces: tuple[str, ...]


def surface_name_for_content_type(content_type: ContentType) -> str:
    """Map an effective content type to its client surface."""
    if content_type == ContentType.ARTICLE:
        return "articles"
    if content_type == ContentType.REEL:
        return "reels"
    return "videos"


def surface_name_for_item(item: Any) -> str:
    """Resolve the client surface for a persisted item."""
    return surface_name_for_content_type(effective_content_type(item))


def describe_readiness_reason(reason: str | None) -> str:
    """Return an operator-facing explanation for a readiness reason code."""
    normalized = _trimmed(reason)
    if not normalized:
        return "Readiness has not been evaluated yet."
    return _READINESS_REASON_DESCRIPTIONS.get(
        normalized,
        normalized.replace("_", " ").capitalize() + ".",
    )


def ready_content_filter(surface_name: str):
    """Shared SQL filter for user-facing, ready-for-delivery content."""
    return and_(
        surface_content_filter(surface_name),
        visible_promotion_filter(),
        ContentItem.is_suppressed.is_(False),
        ContentItem.curation_status == ContentStatus.PROMOTED,
        ContentItem.readiness_status == ContentReadinessStatus.READY.value,
    )


def is_ready_for_surface(item: Any, surface_name: str) -> bool:
    """Return True when the item is ready for the requested client surface."""
    decision = evaluate_content_readiness(item)
    return decision.status == ContentReadinessStatus.READY and surface_name in decision.surfaces


def evaluate_content_readiness(item: Any) -> ContentReadinessDecision:
    """Evaluate whether a content item is safe for client delivery."""
    effective_type = effective_content_type(item)
    surfaces = _surfaces_for_type(effective_type)

    if bool(getattr(item, "is_suppressed", False)):
        return ContentReadinessDecision(
            status=ContentReadinessStatus.PENDING,
            reason="suppressed",
            effective_type=effective_type,
            surfaces=surfaces,
        )

    if getattr(item, "curation_status", None) != ContentStatus.PROMOTED:
        return ContentReadinessDecision(
            status=ContentReadinessStatus.PENDING,
            reason="awaiting_promotion",
            effective_type=effective_type,
            surfaces=surfaces,
        )

    if _is_visibility_blocked(item):
        return ContentReadinessDecision(
            status=ContentReadinessStatus.PENDING,
            reason="blocked_promotion",
            effective_type=effective_type,
            surfaces=surfaces,
        )

    if effective_type == ContentType.ARTICLE:
        source_url = _trimmed(getattr(item, "source_url", None)) or _trimmed(
            getattr(item, "canonical_url", None)
        )
        if not source_url:
            return ContentReadinessDecision(
                status=ContentReadinessStatus.PENDING,
                reason="missing_article_source",
                effective_type=effective_type,
                surfaces=surfaces,
            )

        if not bool(getattr(item, "ai_processed", False)):
            return ContentReadinessDecision(
                status=ContentReadinessStatus.PENDING,
                reason="awaiting_ai_processing",
                effective_type=effective_type,
                surfaces=surfaces,
            )

        if not _trimmed(getattr(item, "summary", None)):
            return ContentReadinessDecision(
                status=ContentReadinessStatus.PENDING,
                reason="missing_article_summary",
                effective_type=effective_type,
                surfaces=surfaces,
            )

        return ContentReadinessDecision(
            status=ContentReadinessStatus.READY,
            reason="article_ready",
            effective_type=effective_type,
            surfaces=surfaces,
        )

    title = _trimmed(getattr(item, "title", None))
    if not title:
        return ContentReadinessDecision(
            status=ContentReadinessStatus.PENDING,
            reason="missing_video_title",
            effective_type=effective_type,
            surfaces=surfaces,
        )

    delivery_url = _trimmed(getattr(item, "video_url", None)) or _trimmed(
        getattr(item, "source_url", None)
    )
    if not delivery_url:
        return ContentReadinessDecision(
            status=ContentReadinessStatus.PENDING,
            reason="missing_video_url",
            effective_type=effective_type,
            surfaces=surfaces,
        )

    ready_reason = "reel_ready" if effective_type == ContentType.REEL else "video_ready"
    return ContentReadinessDecision(
        status=ContentReadinessStatus.READY,
        reason=ready_reason,
        effective_type=effective_type,
        surfaces=surfaces,
    )


def seed_content_readiness(item: Any, *, now: datetime | None = None) -> ContentReadinessSyncResult:
    """Populate readiness fields for a transient or already-loaded item."""
    return sync_content_readiness(None, item, emit_ready_event=False, now=now)


def sync_content_readiness(
    db: Session | None,
    item: Any,
    *,
    emit_ready_event: bool = True,
    now: datetime | None = None,
) -> ContentReadinessSyncResult:
    """Recompute persisted readiness fields and optionally enqueue a ready event."""
    ts = now or datetime.utcnow()
    previous_status = _normalize_status(getattr(item, "readiness_status", None))
    decision = evaluate_content_readiness(item)

    item.readiness_status = decision.status.value
    item.readiness_reason = decision.reason
    item.readiness_updated_at = ts

    if decision.status == ContentReadinessStatus.READY:
        if getattr(item, "ready_at", None) is None:
            item.ready_at = ts
        if emit_ready_event and previous_status != ContentReadinessStatus.READY and db is not None:
            queue_content_ready_event(db, item, decision, now=ts)
    else:
        item.ready_at = None

    return ContentReadinessSyncResult(
        previous_status=previous_status,
        current_status=decision.status,
        reason=decision.reason,
        transitioned_to_ready=(
            previous_status != ContentReadinessStatus.READY
            and decision.status == ContentReadinessStatus.READY
        ),
        surfaces=decision.surfaces,
    )


def queue_content_ready_event(
    db: Session,
    item: Any,
    decision: ContentReadinessDecision | None = None,
    *,
    now: datetime | None = None,
) -> ContentEventOutbox | None:
    """Enqueue a durable outbox event for a ready content item."""
    if getattr(item, "id", None) is None:
        return None

    readiness = decision or evaluate_content_readiness(item)
    if readiness.status != ContentReadinessStatus.READY:
        return None

    event = ContentEventOutbox(
        content_item_id=int(item.id),
        event_type=CONTENT_READY_EVENT_TYPE,
        payload=build_content_ready_event_payload(item, readiness),
        status="pending",
        available_at=now or datetime.utcnow(),
    )
    db.add(event)
    return event


def build_content_ready_event_payload(
    item: Any,
    decision: ContentReadinessDecision | None = None,
) -> dict[str, Any]:
    """Build the durable payload for a ready-content outbox event."""
    readiness = decision or evaluate_content_readiness(item)
    published_at = getattr(item, "published_at", None)
    created_at = getattr(item, "created_at", None)
    ready_at = getattr(item, "ready_at", None)
    curation_status = getattr(item, "curation_status", None)

    return {
        "content_id": int(item.id),
        "effective_type": readiness.effective_type.value,
        "surfaces": list(readiness.surfaces),
        "curation_status": getattr(curation_status, "value", curation_status),
        "published_at": published_at.isoformat() if published_at else None,
        "created_at": created_at.isoformat() if created_at else None,
        "ready_at": ready_at.isoformat() if ready_at else None,
        "readiness_reason": readiness.reason,
    }


def _normalize_status(value: Any) -> ContentReadinessStatus:
    if isinstance(value, ContentReadinessStatus):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized == ContentReadinessStatus.READY.value:
            return ContentReadinessStatus.READY
    return ContentReadinessStatus.PENDING


def _surfaces_for_type(content_type: ContentType) -> tuple[str, ...]:
    return (surface_name_for_content_type(content_type),)


def _trimmed(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _is_visibility_blocked(item: Any) -> bool:
    promotion_reason = _trimmed(getattr(item, "promotion_reason", None))
    return "|blocked=" in promotion_reason
