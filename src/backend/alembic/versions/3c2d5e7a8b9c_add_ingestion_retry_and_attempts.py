"""add_ingestion_retry_and_attempts

Revision ID: 3c2d5e7a8b9c
Revises: 9f1c2a3b4c5d
Create Date: 2026-01-29

MIGRATION POLICY: ADDITIVE ONLY
- Adds retry/backoff and attempted counters to ingestion_progress
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "3c2d5e7a8b9c"
down_revision = "9f1c2a3b4c5d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingestion_progress",
        sa.Column("items_attempted", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ingestion_progress",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ingestion_progress",
        sa.Column("retry_at", sa.DateTime(), nullable=True),
    )

    op.create_index(
        "ix_ingestion_progress_retry_at",
        "ingestion_progress",
        ["day_utc", "retry_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_progress_retry_at", table_name="ingestion_progress")
    op.drop_column("ingestion_progress", "retry_at")
    op.drop_column("ingestion_progress", "retry_count")
    op.drop_column("ingestion_progress", "items_attempted")
