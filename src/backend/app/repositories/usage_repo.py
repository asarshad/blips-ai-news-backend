"""
Usage repository for database operations on user usage tracking.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.usage import Usage
from app.repositories.base import BaseRepository


class UsageRepository(BaseRepository[Usage]):
    """Repository for Usage model database operations."""

    def __init__(self, db: Session):
        super().__init__(db, Usage)

    def get_daily_usage(self, device_id: str) -> int:
        """Get total message count for a device today."""
        today = datetime.utcnow().date()
        tomorrow = today + timedelta(days=1)

        result = (
            self.db.query(func.sum(Usage.message_count))
            .filter(
                Usage.device_id == device_id, Usage.timestamp >= today, Usage.timestamp < tomorrow
            )
            .scalar()
        )

        return result or 0

    def get_content_usage(self, device_id: str, content_item_id: int) -> int:
        """Get total message count for a device on a specific content item."""
        result = (
            self.db.query(func.sum(Usage.message_count))
            .filter(Usage.device_id == device_id, Usage.content_item_id == content_item_id)
            .scalar()
        )

        return result or 0

    def get_article_usage(self, device_id: str, article_id: int) -> int:
        """Backward-compatible alias for per-content quota checks."""
        return self.get_content_usage(device_id, article_id)

    def record_usage(
        self, device_id: str, content_item_id: Optional[int] = None, tokens: int = 0
    ) -> Usage:
        """Record a new usage entry."""
        usage = Usage(
            device_id=device_id,
            content_item_id=content_item_id,
            used_tokens=tokens,
            message_count=1,
            timestamp=datetime.utcnow(),
        )
        self.db.add(usage)
        self.db.commit()
        self.db.refresh(usage)
        return usage

    def get_total_tokens_used(self, device_id: str) -> int:
        """Get total tokens used by a device."""
        result = (
            self.db.query(func.sum(Usage.used_tokens)).filter(Usage.device_id == device_id).scalar()
        )

        return result or 0
