"""
Repository layer for editorial operations.

Provides data-access helpers for editorial content management and
the editorial_actions audit log.  No business logic lives here —
see domain/editorial/service.py for that.
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc
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
                ContentItem.published_at >= start,
                ContentItem.published_at < end,
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
            query = query.order_by(desc(ContentItem.editorial_boost), desc(ContentItem.published_at))
        else:
            query = query.order_by(desc(ContentItem.published_at))

        offset = (page - 1) * page_size
        items = query.offset(offset).limit(page_size).all()
        return items, total

    def get_content_by_id(self, content_id: int) -> Optional[ContentItem]:
        return self.db.query(ContentItem).filter(ContentItem.id == content_id).first()

    def get_by_canonical_key(self, canonical_key: str) -> Optional[ContentItem]:
        return (
            self.db.query(ContentItem)
            .filter(ContentItem.canonical_key == canonical_key)
            .first()
        )

    def get_by_source_url(self, source_url: str) -> Optional[ContentItem]:
        return (
            self.db.query(ContentItem)
            .filter(ContentItem.source_url == source_url)
            .first()
        )

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
