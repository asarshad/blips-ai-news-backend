"""Add major-news classifier metadata to content_items

Revision ID: major_news_fast_path_001
Revises: clustering_event_002
Create Date: 2026-05-03

MIGRATION POLICY: ADDITIVE ONLY
- Adds nullable major-tech-news classifier fields for the article fast path.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "major_news_fast_path_001"
down_revision = "clustering_event_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("content_items")}

    if "is_major_tech_news" not in existing:
        op.add_column(
            "content_items",
            sa.Column("is_major_tech_news", sa.Boolean(), nullable=True),
        )
    if "major_tech_news_confidence" not in existing:
        op.add_column(
            "content_items",
            sa.Column("major_tech_news_confidence", sa.Float(), nullable=True),
        )
    if "major_tech_news_reason" not in existing:
        op.add_column(
            "content_items",
            sa.Column("major_tech_news_reason", sa.String(length=255), nullable=True),
        )

    indexes = {idx["name"] for idx in inspector.get_indexes("content_items")}
    if "ix_content_items_is_major_tech_news" not in indexes:
        op.create_index(
            "ix_content_items_is_major_tech_news",
            "content_items",
            ["is_major_tech_news"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_content_items_is_major_tech_news", table_name="content_items")
    op.drop_column("content_items", "major_tech_news_reason")
    op.drop_column("content_items", "major_tech_news_confidence")
    op.drop_column("content_items", "is_major_tech_news")
