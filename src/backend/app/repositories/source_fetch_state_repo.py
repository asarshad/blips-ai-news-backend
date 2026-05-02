"""Repository for durable external source fetch state."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.ingestion.source_response_policy import (
    FetchOutcome,
    cooldown_until_for_outcome,
    health_status_for_outcome,
)
from app.models.source_fetch_state import SourceFetchState


class SourceFetchStateRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, *, source_type: str, feed_name: str) -> Optional[SourceFetchState]:
        return (
            self.db.query(SourceFetchState)
            .filter(
                SourceFetchState.source_type == source_type,
                SourceFetchState.feed_name == feed_name,
            )
            .one_or_none()
        )

    def get_active_cooldown(
        self,
        *,
        source_type: str,
        feed_name: str,
        now: Optional[datetime] = None,
    ) -> Optional[SourceFetchState]:
        current = now or datetime.utcnow()
        row = self.get(source_type=source_type, feed_name=feed_name)
        if row is None or row.cooldown_until is None:
            return None
        if row.cooldown_until <= current:
            return None
        return row

    def record_outcome(
        self,
        *,
        source_type: str,
        feed_name: str,
        source_url: str,
        outcome: FetchOutcome,
        now: Optional[datetime] = None,
    ) -> SourceFetchState:
        current = now or datetime.utcnow()
        row = self.get(source_type=source_type, feed_name=feed_name)
        if row is None:
            row = SourceFetchState(
                source_type=source_type,
                feed_name=feed_name,
                source_url=source_url,
                created_at=current,
            )
            self.db.add(row)
            self.db.flush()

        consecutive_failures = 0 if outcome.succeeded else int(row.consecutive_failure_count or 0) + 1
        row.source_url = source_url
        row.health_status = health_status_for_outcome(outcome)
        row.last_action = outcome.action
        row.last_http_status = outcome.status_code
        row.last_error = outcome.error
        row.retry_after_seconds = outcome.retry_after_seconds
        row.cooldown_until = cooldown_until_for_outcome(
            outcome,
            now=current,
            consecutive_failures=consecutive_failures,
        )
        row.canonical_url = outcome.canonical_url or row.canonical_url
        row.redirect_count = int(outcome.redirect_count or 0)
        row.updated_at = current

        if outcome.succeeded:
            row.success_count = int(row.success_count or 0) + 1
            row.consecutive_failure_count = 0
            row.last_success_at = current
        else:
            row.failure_count = int(row.failure_count or 0) + 1
            row.consecutive_failure_count = consecutive_failures
            row.last_failure_at = current

        self.db.commit()
        return row
