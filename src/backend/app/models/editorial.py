"""
Editorial action audit log model.

Append-only table recording every editorial action (add, boost,
suppress, unsuppress) for audit and compliance purposes.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class EditorialAction(Base):
    """
    Append-only audit log of editorial actions on content items.

    Every boost, suppress, add, or unsuppress is recorded here with
    before/after values and the actor who performed it.
    """

    __tablename__ = "editorial_actions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    content_id = Column(
        BigInteger,
        ForeignKey("content_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_type = Column(Text, nullable=False)   # ADD, BOOST, SUPPRESS, UNSUPPRESS
    old_value = Column(JSONB, nullable=True)
    new_value = Column(JSONB, nullable=True)
    actor = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_editorial_actions_content_created", "content_id", created_at.desc()),
    )

    def __repr__(self) -> str:
        return (
            f"<EditorialAction(id={self.id}, content_id={self.content_id}, "
            f"action={self.action_type}, actor={self.actor})>"
        )
