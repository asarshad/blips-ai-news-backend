"""Dispatch durable content lifecycle events from the outbox."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.models.content_event import ContentEventOutbox
from app.services.content_ai_service import (
    CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE,
    process_content_ai_summary_request,
)
from app.services.content_promotion_service import (
    CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE,
    process_content_promotion_request,
)
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


class _DeferredDispatch(Exception):
    """Signal that an outbox event should be retried later without counting as a failure."""

    def __init__(self, *, reason: str, delay_seconds: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.delay_seconds = max(1, int(delay_seconds))


def _promotion_fetch_deferral_seconds() -> int:
    raw = os.getenv("CONTENT_PROMOTION_FETCH_DEFERRAL_SECONDS", "60")
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(
            "Invalid CONTENT_PROMOTION_FETCH_DEFERRAL_SECONDS=%r; defaulting to 60",
            raw,
        )
        return 60


def _is_fetch_news_active() -> bool:
    """Check scheduler runtime state without importing the scheduler package."""
    runtime_module = sys.modules.get("app.scheduler.runtime")
    if runtime_module is None:
        return False
    fetch_news_job = getattr(runtime_module, "FETCH_NEWS_JOB", "fetch_news")
    is_job_active = getattr(runtime_module, "is_job_active", None)
    if not callable(is_job_active):
        return False
    try:
        return bool(is_job_active(fetch_news_job))
    except Exception:
        return False


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
        self._event_types = tuple(event_types or ())

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
            query = db.query(ContentEventOutbox).filter(
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
            if self._event_types:
                query = query.filter(ContentEventOutbox.event_type.in_(self._event_types))
            rows = (
                query.order_by(ContentEventOutbox.created_at.asc())
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
        deferred: _DeferredDispatch | None = None
        deferred_event_type = "unknown"
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
            deferred_event_type = getattr(event, "event_type", "unknown")

            try:
                self._dispatch(event, db)
                event.status = "processed"
                event.processed_at = datetime.utcnow()
                event.locked_at = None
                event.last_error = None
                event.updated_at = datetime.utcnow()
                db.commit()
                return True
            except _DeferredDispatch as exc:
                deferred = exc
                db.rollback()
            except Exception as exc:
                error_message = str(exc)
                logger.warning(
                    "Content event dispatch failed for event %s (%s): %s",
                    event.id,
                    event.event_type,
                    exc,
                )
                db.rollback()

        if deferred is not None:
            with self._session_factory() as db:
                event = db.query(ContentEventOutbox).filter(ContentEventOutbox.id == event_id).first()
                if event is None:
                    return False
                event.status = "pending"
                event.locked_at = None
                event.available_at = datetime.utcnow() + timedelta(seconds=deferred.delay_seconds)
                event.last_error = None
                event.attempt_count = max(0, int(event.attempt_count or 0) - 1)
                event.updated_at = datetime.utcnow()
                db.commit()
            logger.info(
                "Deferred content event %s (%s): %s; retrying in %ss",
                event_id,
                deferred_event_type,
                deferred.reason,
                deferred.delay_seconds,
            )
            return False

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
        elif event.event_type == CONTENT_AI_SUMMARY_REQUESTED_EVENT_TYPE:
            self._dispatch_content_ai_summary_event(event, db)
        elif event.event_type == CONTENT_PROMOTION_EVAL_REQUESTED_EVENT_TYPE:
            self._dispatch_content_promotion_event(event, db)

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

    def _dispatch_content_ai_summary_event(
        self,
        event: ContentEventOutbox,
        db: Session,
    ) -> None:
        payload = dict(event.payload or {})
        content_id = int(payload.get("content_id") or event.content_item_id)
        result = process_content_ai_summary_request(db, content_id=content_id)
        logger.info(
            "[content_events] content ai summary content_id=%s changed=%s skipped=%s ai_processed=%s readiness=%s",
            content_id,
            result.get("changed"),
            result.get("skipped"),
            result.get("ai_processed"),
            result.get("readiness_status"),
        )

    def _dispatch_content_promotion_event(
        self,
        event: ContentEventOutbox,
        db: Session,
    ) -> None:
        if _is_fetch_news_active():
            raise _DeferredDispatch(
                reason="fetch_news_active",
                delay_seconds=_promotion_fetch_deferral_seconds(),
            )
        payload = dict(event.payload or {})
        content_id = int(payload.get("content_id") or event.content_item_id)
        result = process_content_promotion_request(db, content_id=content_id)
        logger.info(
            "[content_events] content promotion content_id=%s changed=%s skipped=%s promoted=%s score=%s",
            content_id,
            result.get("changed"),
            result.get("skipped"),
            result.get("promoted"),
            result.get("promotion_score"),
        )


def _retry_delay(attempt_count: int | None) -> timedelta:
    attempt = max(1, int(attempt_count or 1))
    return timedelta(seconds=min(300, 15 * attempt))
