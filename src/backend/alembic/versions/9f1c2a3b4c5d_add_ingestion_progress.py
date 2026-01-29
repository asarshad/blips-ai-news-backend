"""add_ingestion_progress

Revision ID: 9f1c2a3b4c5d
Revises: 4b89bee76a66
Create Date: 2026-01-29

MIGRATION POLICY: ADDITIVE ONLY
- Adds ingestion_progress table for restart-resilient ingestion checkpointing
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9f1c2a3b4c5d"
down_revision = "4b89bee76a66"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day_utc", sa.Date(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("feed_name", sa.String(length=255), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.Column("items_ingested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_item_cursor", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint(
            "day_utc",
            "source_type",
            "feed_name",
            name="uq_ingestion_progress_day_source_feed",
        ),
    )

    op.create_index(
        "ix_ingestion_progress_day_utc",
        "ingestion_progress",
        ["day_utc"],
    )
    op.create_index(
        "ix_ingestion_progress_scope",
        "ingestion_progress",
        ["day_utc", "source_type", "feed_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_progress_scope", table_name="ingestion_progress")
    op.drop_index("ix_ingestion_progress_day_utc", table_name="ingestion_progress")
    op.drop_table("ingestion_progress")
