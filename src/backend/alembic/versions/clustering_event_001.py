"""Allow null content_item_id on content_event_outbox for global events.

Revision ID: clustering_event_001
Revises: source_fetch_state_001
Create Date: 2026-05-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "clustering_event_001"
down_revision = "source_fetch_state_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "content_event_outbox",
        "content_item_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "content_event_outbox",
        "content_item_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
