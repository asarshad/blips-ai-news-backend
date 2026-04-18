"""Dispatch durable content lifecycle events from the outbox."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.models.content_event import ContentEventOutbox
from app.services.article_image_service import (
    ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE,
    process_article_image_verification_request,
)
from app.services.playlist_service import refresh_cached_playlist_items
from app.services.content_readiness import CONTENT_READY_EVENT_TYPE, CONTENT_UNREADY_EVENT_TYPE
from app.services.inventory_service import Surface
from app.services.push_service import PushNotificationService
from app.services.tiered_feed_service import invalidate_tiered_feed_cache

logger = get_logger(__name__)


class ContentEventDispatcher:
    """Claim and process content lifecycle events from the outbox."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker | None = None,
        lock_timeout: timedelta = timedelta(minutes=10),
        event_types: tuple[str, ...] | None = None,
    ) -> None:
        self._session_factory = session_factory or SessionLocal
        self._lock_timeout = lock_timeout
        self._event_types = tuple(event_types) if event_types else None

    def process_pending(self, *, limit: int = 50) -> int:
        """Process a bounded batch of pending outbox events."""
        claimed_ids = self._claim_pending(limit=limit)
        processed = 0
        for event_id in claimed_ids:
            if self._process_claimed(event_id):
                processed += 1
        return processed

    def _claim_pending(self, *, limit: int) -> list[int]:
        now = datetime.utcnow()
        stale_before = now - self._lock_timeout

        with self._session_factory() as db:
            rows = (
                db.query(ContentEventOutbox)
                .filter(
                    or_(
                        and_(
                            ContentEventOutbox.status == "pending",
                            ContentEventOutbox.available_at <= now,
                        ),
                        and_(
                            ContentEventOutbox.status == "processing",
                            ContentEventOutbox.locked_at.is_not(None),
                            ContentEventOutbox.locked_at < stale_before,
                        ),
                    )
                )
                .filter(
                    ContentEventOutbox.event_type.in_(self._event_types)
                    if self._event_types
                    else True
                )
                .order_by(ContentEventOutbox.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(limit)
                .all()
            )

            claimed_ids: list[int] = []
            for row in rows:
                row.status = "processing"
                row.locked_at = now
                row.attempt_count = int(row.attempt_count or 0) + 1
                row.updated_at = now
                claimed_ids.append(int(row.id))

            if claimed_ids:
                db.commit()
            return claimed_ids

    def _process_claimed(self, event_id: int) -> bool:
        error_message = "dispatch failed"
        with self._session_factory() as db:
            event = (
                db.query(ContentEventOutbox)
                .filter(
                    ContentEventOutbox.id == event_id,
                    ContentEventOutbox.status == "processing",
                )
                .first()
            )
            if event is None:
                return False

            try:
                self._dispatch(event, db)
                event.status = "processed"
                event.processed_at = datetime.utcnow()
                event.locked_at = None
                event.last_error = None
                event.updated_at = datetime.utcnow()
                db.commit()
                return True
            except Exception as exc:
                error_message = str(exc)
                logger.warning(
                    "Content event dispatch failed for event %s (%s): %s",
                    event.id,
                    event.event_type,
                    exc,
                )
                db.rollback()

        with self._session_factory() as db:
            event = db.query(ContentEventOutbox).filter(ContentEventOutbox.id == event_id).first()
            if event is None:
                return False
            event.status = "pending"
            event.locked_at = None
            event.available_at = datetime.utcnow() + _retry_delay(event.attempt_count)
            event.last_error = error_message[:4000]
            event.updated_at = datetime.utcnow()
            db.commit()
        return False

    def _dispatch(self, event: ContentEventOutbox, db: Session) -> None:
        if event.event_type == CONTENT_READY_EVENT_TYPE:
            self._dispatch_ready_event(event, db)
        elif event.event_type == CONTENT_UNREADY_EVENT_TYPE:
            self._dispatch_unready_event(event)
        elif event.event_type == ARTICLE_IMAGE_VERIFY_REQUESTED_EVENT_TYPE:
            self._dispatch_article_image_verification_event(event, db)

    def _invalidate_surfaces(self, payload: dict) -> None:
        for surface_name in payload.get("surfaces", []):
            try:
                invalidate_tiered_feed_cache(surface=Surface(surface_name))
            except Exception:
                invalidate_tiered_feed_cache()
                break

    def _dispatch_ready_event(self, event: ContentEventOutbox, db: Session) -> None:
        payload = dict(event.payload or {})
        content_id = int(payload.get("content_id") or event.content_item_id)
        self._invalidate_surfaces(payload)
        refresh_cached_playlist_items(db, content_ids=[content_id])

        PushNotificationService(db=db).send_auto_for_content_ids(
            [content_id],
            actor=f"event:{CONTENT_READY_EVENT_TYPE}",
        )

    def _dispatch_unready_event(self, event: ContentEventOutbox) -> None:
        self._invalidate_surfaces(dict(event.payload or {}))

    def _dispatch_article_image_verification_event(
        self,
        event: ContentEventOutbox,
        db: Session,
    ) -> None:
        payload = dict(event.payload or {})
        content_id = int(payload.get("content_id") or event.content_item_id)
        result = process_article_image_verification_request(db, content_id=content_id)
        if result.get("changed"):
            refresh_cached_playlist_items(db, content_ids=[content_id])
        logger.info(
            "[content_events] article image verification content_id=%s changed=%s status=%s readiness=%s",
            content_id,
            result.get("changed"),
            result.get("article_image_status"),
            result.get("readiness_status"),
        )


def _retry_delay(attempt_count: int | None) -> timedelta:
    attempt = max(1, int(attempt_count or 1))
    return timedelta(seconds=min(300, 15 * attempt))
