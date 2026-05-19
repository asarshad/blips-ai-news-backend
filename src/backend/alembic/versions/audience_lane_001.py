"""Add audience_lane classification columns to content_items.

Revision ID: audience_lane_001
Revises: x_signal_source_001
Create Date: 2026-05-15

Adds three nullable columns:
  audience_lane          VARCHAR(16)  — "GENERAL_PUBLIC" | "TECHIES" | NULL
  audience_lane_confidence FLOAT      — LLM confidence 0.0–1.0
  audience_lane_reason   VARCHAR(255) — short LLM explanation
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "audience_lane_001"
down_revision = "major_news_fast_path_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column("audience_lane", sa.String(32), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("audience_lane_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("audience_lane_reason", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_content_audience_lane",
        "content_items",
        ["audience_lane"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_audience_lane", table_name="content_items")
    op.drop_column("content_items", "audience_lane_reason")
    op.drop_column("content_items", "audience_lane_confidence")
    op.drop_column("content_items", "audience_lane")
