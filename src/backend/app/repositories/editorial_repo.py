"""
Repository layer for editorial operations.

Provides data-access helpers for editorial content management and
the editorial_actions audit log.  No business logic lives here —
see domain/editorial/service.py for that.
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.editorial import EditorialAction


class EditorialRepository:
    """Data access for editorial/admin operations."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Content queries
    # ------------------------------------------------------------------

    def list_content(
        self,
        *,
        day: Optional[date] = None,
        content_type: Optional[str] = None,
        source: Optional[str] = None,
        suppressed: Optional[bool] = None,
        manual_added: Optional[bool] = None,
        curation_status: Optional[str] = None,
        sort_by: str = "published_at",
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[ContentItem], int]:
        """Return a filtered, paginated list of content items + total count."""
        query = self.db.query(ContentItem)

        if day is not None:
            start = datetime.combine(day, datetime.min.time())
            end = start + timedelta(days=1)
            query = query.filter(
                or_(
                    ContentItem.ingestion_day == day,
                    and_(
                        ContentItem.ingestion_day.is_(None),
                        ContentItem.published_at >= start,
                        ContentItem.published_at < end,
                    ),
                ),
            )

        if content_type is not None:
            ct = content_type.upper()
            if ct in ContentType.__members__:
                query = query.filter(ContentItem.type == ContentType[ct])

        if source is not None:
            query = query.filter(ContentItem.source.ilike(f"%{source}%"))

        if suppressed is not None:
            query = query.filter(ContentItem.is_suppressed == suppressed)

        if manual_added is not None:
            query = query.filter(ContentItem.manual_added == manual_added)

        if curation_status is not None:
            cs = curation_status.upper()
            if cs in ContentStatus.__members__:
                query = query.filter(ContentItem.curation_status == ContentStatus[cs])

        total = query.count()

        # Sorting
        if sort_by == "created_at":
            query = query.order_by(desc(ContentItem.created_at))
        elif sort_by == "editorial_boost":
            query = query.order_by(
                desc(ContentItem.editorial_boost), desc(ContentItem.published_at)
            )
        else:
            query = query.order_by(desc(ContentItem.published_at))

        offset = (page - 1) * page_size
        items = query.offset(offset).limit(page_size).all()
        return items, total

    def list_candidate_queue(
        self,
        *,
        content_type: Optional[str] = None,
        source: Optional[str] = None,
        discovered_via: Optional[str] = None,
        min_signal_hits: int = 0,
        start_day: Optional[date] = None,
        end_day: Optional[date] = None,
        curation_status: Optional[str] = "CANDIDATE",
        include_suppressed: bool = False,
        sort_by: str = "priority",
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[ContentItem], int]:
        """Return paginated review queue items ordered for triage."""
        query = self.db.query(ContentItem)

        if curation_status is not None:
            cs = curation_status.upper()
            if cs in ContentStatus.__members__:
                query = query.filter(ContentItem.curation_status == ContentStatus[cs])
            else:
                query = query.filter(ContentItem.curation_status == ContentStatus.CANDIDATE)

        if not include_suppressed:
            query = query.filter(ContentItem.is_suppressed.is_(False))

        if content_type is not None:
            ct = content_type.upper()
            if ct in ContentType.__members__:
                query = query.filter(ContentItem.type == ContentType[ct])

        if source is not None:
            query = query.filter(ContentItem.source.ilike(f"%{source}%"))

        if discovered_via is not None:
            query = query.filter(ContentItem.discovered_via.ilike(f"%{discovered_via}%"))

        if min_signal_hits > 0:
            query = query.filter(ContentItem.signal_hits >= int(min_signal_hits))

        if start_day is not None or end_day is not None:
            first_seen_expr = func.coalesce(
                ContentItem.candidate_first_seen_at,
                ContentItem.published_at,
            )
            if start_day is not None:
                start_dt = datetime.combine(start_day, datetime.min.time())
                query = query.filter(first_seen_expr >= start_dt)
            if end_day is not None:
                end_dt = datetime.combine(end_day + timedelta(days=1), datetime.min.time())
                query = query.filter(first_seen_expr < end_dt)

        total = query.count()
        first_seen_col = getattr(ContentItem, "candidate_first_seen_at", None)

        if sort_by == "first_seen":
            if first_seen_col is not None:
                query = query.order_by(
                    desc(first_seen_col).nullslast(),
                    desc(ContentItem.published_at),
                )
            else:
                query = query.order_by(desc(ContentItem.published_at))
        elif sort_by == "published_at":
            query = query.order_by(desc(ContentItem.published_at))
        else:
            ordering = [
                desc(ContentItem.promotion_score).nullslast(),
                desc(ContentItem.signal_hits),
            ]
            if first_seen_col is not None:
                ordering.append(desc(first_seen_col).nullslast())
            ordering.append(desc(ContentItem.published_at))
            query = query.order_by(*ordering)

        offset = (page - 1) * page_size
        items = query.offset(offset).limit(page_size).all()
        return items, total

    def candidate_queue_counts(
        self,
        *,
        include_suppressed: bool = False,
        curation_status: Optional[str] = "CANDIDATE",
    ) -> Dict[str, int]:
        """Return review queue counts grouped by content type."""
        query = self.db.query(ContentItem.type, func.count(ContentItem.id))
        if curation_status is not None:
            cs = curation_status.upper()
            if cs in ContentStatus.__members__:
                query = query.filter(ContentItem.curation_status == ContentStatus[cs])
            else:
                query = query.filter(ContentItem.curation_status == ContentStatus.CANDIDATE)
        if not include_suppressed:
            query = query.filter(ContentItem.is_suppressed.is_(False))

        rows = query.group_by(ContentItem.type).all()

        counts: Dict[str, int] = {}
        for content_type, count in rows:
            key = content_type.value if content_type is not None else "UNKNOWN"
            counts[key] = int(count or 0)
        return counts

    def get_content_by_id(self, content_id: int) -> Optional[ContentItem]:
        return self.db.query(ContentItem).filter(ContentItem.id == content_id).first()

    def get_by_canonical_key(self, canonical_key: str) -> Optional[ContentItem]:
        return self.db.query(ContentItem).filter(ContentItem.canonical_key == canonical_key).first()

    def get_by_source_url(self, source_url: str) -> Optional[ContentItem]:
        return self.db.query(ContentItem).filter(ContentItem.source_url == source_url).first()

    # ------------------------------------------------------------------
    # Editorial mutations
    # ------------------------------------------------------------------

    def set_boost(self, content_id: int, level: int, actor: str) -> Optional[ContentItem]:
        item = self.get_content_by_id(content_id)
        if item is None:
            return None
        old_boost = item.editorial_boost
        item.editorial_boost = level
        item.last_modified_by = actor
        item.last_modified_at = datetime.now(tz=None)
        self._log_action(
            content_id=content_id,
            action_type="BOOST",
            old_value={"editorial_boost": old_boost},
            new_value={"editorial_boost": level},
            actor=actor,
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def suppress(self, content_id: int, actor: str) -> Optional[ContentItem]:
        item = self.get_content_by_id(content_id)
        if item is None:
            return None
        if item.is_suppressed:
            return item  # idempotent
        item.is_suppressed = True
        item.last_modified_by = actor
        item.last_modified_at = datetime.now(tz=None)
        self._log_action(
            content_id=content_id,
            action_type="SUPPRESS",
            old_value={"is_suppressed": False},
            new_value={"is_suppressed": True},
            actor=actor,
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def unsuppress(self, content_id: int, actor: str) -> Optional[ContentItem]:
        item = self.get_content_by_id(content_id)
        if item is None:
            return None
        if not item.is_suppressed:
            return item  # idempotent
        item.is_suppressed = False
        item.last_modified_by = actor
        item.last_modified_at = datetime.now(tz=None)
        self._log_action(
            content_id=content_id,
            action_type="UNSUPPRESS",
            old_value={"is_suppressed": True},
            new_value={"is_suppressed": False},
            actor=actor,
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def promote(self, content_id: int, actor: str) -> Optional[ContentItem]:
        """Manually promote a CANDIDATE item to PROMOTED."""
        item = self.get_content_by_id(content_id)
        if item is None:
            return None
        old_status = item.curation_status
        if old_status == ContentStatus.PROMOTED:
            return item  # idempotent
        item.curation_status = ContentStatus.PROMOTED
        item.last_modified_by = actor
        item.last_modified_at = datetime.now(tz=None)
        self._log_action(
            content_id=content_id,
            action_type="PROMOTE",
            old_value={"curation_status": old_status.value if old_status else None},
            new_value={"curation_status": ContentStatus.PROMOTED.value},
            actor=actor,
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def demote(self, content_id: int, actor: str) -> Optional[ContentItem]:
        """Manually demote a PROMOTED item back to CANDIDATE."""
        item = self.get_content_by_id(content_id)
        if item is None:
            return None
        old_status = item.curation_status
        if old_status == ContentStatus.CANDIDATE:
            return item  # idempotent
        item.curation_status = ContentStatus.CANDIDATE
        item.last_modified_by = actor
        item.last_modified_at = datetime.now(tz=None)
        self._log_action(
            content_id=content_id,
            action_type="DEMOTE",
            old_value={"curation_status": old_status.value if old_status else None},
            new_value={"curation_status": ContentStatus.CANDIDATE.value},
            actor=actor,
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def _set_review_state(
        self,
        *,
        content_id: int,
        actor: str,
        action_type: str,
        curation_status: Optional[ContentStatus] = None,
        suppressed: Optional[bool] = None,
        note: Optional[str] = None,
    ) -> Optional[ContentItem]:
        """Apply editorial review state transition + audit log entry."""
        item = self.get_content_by_id(content_id)
        if item is None:
            return None

        old_state = {
            "curation_status": item.curation_status.value if item.curation_status else None,
            "is_suppressed": bool(item.is_suppressed),
        }

        if curation_status is not None:
            item.curation_status = curation_status
        if suppressed is not None:
            item.is_suppressed = suppressed

        item.last_modified_by = actor
        item.last_modified_at = datetime.now(tz=None)

        new_state = {
            "curation_status": item.curation_status.value if item.curation_status else None,
            "is_suppressed": bool(item.is_suppressed),
        }
        if note:
            new_state["note"] = note

        self._log_action(
            content_id=content_id,
            action_type=action_type,
            old_value=old_state,
            new_value=new_state,
            actor=actor,
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def approve(
        self, content_id: int, actor: str, note: Optional[str] = None
    ) -> Optional[ContentItem]:
        """Approve a candidate and promote it for feed visibility."""
        return self._set_review_state(
            content_id=content_id,
            actor=actor,
            action_type="APPROVE",
            curation_status=ContentStatus.PROMOTED,
            suppressed=False,
            note=note,
        )

    def approve_and_publish(
        self,
        content_id: int,
        actor: str,
        *,
        boost_level: int = 3,
        note: Optional[str] = None,
    ) -> Optional[ContentItem]:
        """
        Approve content and publish it to the top of feed ordering.

        Promotion is applied by:
        - setting curation_status to PROMOTED
        - clearing suppression
        - bumping published_at to now
        - applying at least the provided editorial boost
        """
        item = self.get_content_by_id(content_id)
        if item is None:
            return None

        old_state = {
            "curation_status": item.curation_status.value if item.curation_status else None,
            "is_suppressed": bool(item.is_suppressed),
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "editorial_boost": item.editorial_boost or 0,
        }

        now = datetime.now(tz=None)
        item.curation_status = ContentStatus.PROMOTED
        item.is_suppressed = False
        item.published_at = now
        item.editorial_boost = max(item.editorial_boost or 0, int(boost_level))
        item.last_modified_by = actor
        item.last_modified_at = now

        new_state = {
            "curation_status": item.curation_status.value,
            "is_suppressed": bool(item.is_suppressed),
            "published_at": item.published_at.isoformat(),
            "editorial_boost": item.editorial_boost,
        }
        if note:
            new_state["note"] = note

        self._log_action(
            content_id=content_id,
            action_type="APPROVE_PUBLISH",
            old_value=old_state,
            new_value=new_state,
            actor=actor,
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def reject(
        self, content_id: int, actor: str, note: Optional[str] = None
    ) -> Optional[ContentItem]:
        """Reject content from editorial queue and suppress it."""
        return self._set_review_state(
            content_id=content_id,
            actor=actor,
            action_type="REJECT",
            curation_status=ContentStatus.CANDIDATE,
            suppressed=True,
            note=note,
        )

    def hold(
        self, content_id: int, actor: str, note: Optional[str] = None
    ) -> Optional[ContentItem]:
        """Place content on hold while keeping it available for later review."""
        return self._set_review_state(
            content_id=content_id,
            actor=actor,
            action_type="HOLD",
            curation_status=ContentStatus.CANDIDATE,
            suppressed=False,
            note=note,
        )

    def request_changes(
        self, content_id: int, actor: str, note: Optional[str] = None
    ) -> Optional[ContentItem]:
        """Return content for changes; stays as candidate and unsuppressed."""
        return self._set_review_state(
            content_id=content_id,
            actor=actor,
            action_type="REQUEST_CHANGES",
            curation_status=ContentStatus.CANDIDATE,
            suppressed=False,
            note=note,
        )

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def _log_action(
        self,
        *,
        content_id: int,
        action_type: str,
        old_value: Optional[Dict[str, Any]],
        new_value: Optional[Dict[str, Any]],
        actor: str,
    ) -> EditorialAction:
        action = EditorialAction(
            content_id=content_id,
            action_type=action_type,
            old_value=old_value,
            new_value=new_value,
            actor=actor,
        )
        self.db.add(action)
        return action

    def log_add_action(
        self,
        *,
        content_id: int,
        actor: str,
        url: str,
        importance_level: int,
    ) -> EditorialAction:
        """Record a manual content addition in the audit log."""
        action = self._log_action(
            content_id=content_id,
            action_type="ADD",
            old_value=None,
            new_value={"url": url, "importance_level": importance_level},
            actor=actor,
        )
        self.db.flush()
        return action

    def get_actions_for_content(
        self,
        content_id: int,
        limit: int = 20,
    ) -> List[EditorialAction]:
        return (
            self.db.query(EditorialAction)
            .filter(EditorialAction.content_id == content_id)
            .order_by(desc(EditorialAction.created_at))
            .limit(limit)
            .all()
        )

    def add_reviewer_note(
        self,
        *,
        content_id: int,
        actor: str,
        note: str,
    ) -> Optional[EditorialAction]:
        """Append a reviewer note to the editorial audit log."""
        item = self.get_content_by_id(content_id)
        if item is None:
            return None

        now = datetime.now(tz=None)
        item.last_modified_by = actor
        item.last_modified_at = now

        action = self._log_action(
            content_id=content_id,
            action_type="NOTE",
            old_value=None,
            new_value={"note": note},
            actor=actor,
        )
        self.db.commit()
        return action
