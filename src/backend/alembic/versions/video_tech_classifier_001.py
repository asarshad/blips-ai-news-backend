"""Add video tech-classifier metadata to content_items

Revision ID: video_tech_classifier_001
Revises: article_image_verification_001
Create Date: 2026-03-28

MIGRATION POLICY: ADDITIVE ONLY
- Adds persisted tech relevance metadata for video/reel classifier decisions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "video_tech_classifier_001"
down_revision = "article_image_verification_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("content_items")}

    if "tech_relevance" not in existing:
        op.add_column(
            "content_items",
            sa.Column("tech_relevance", sa.String(length=16), nullable=True),
        )
    if "tech_relevance_confidence" not in existing:
        op.add_column(
            "content_items",
            sa.Column("tech_relevance_confidence", sa.Float(), nullable=True),
        )
    if "tech_relevance_reason" not in existing:
        op.add_column(
            "content_items",
            sa.Column("tech_relevance_reason", sa.String(length=255), nullable=True),
        )
    if "is_mixed_roundup" not in existing:
        op.add_column(
            "content_items",
            sa.Column("is_mixed_roundup", sa.Boolean(), nullable=True),
        )

    indexes = {idx["name"] for idx in inspector.get_indexes("content_items")}
    if "ix_content_items_tech_relevance" not in indexes:
        op.create_index(
            "ix_content_items_tech_relevance",
            "content_items",
            ["tech_relevance"],
            unique=False,
        )
    if "ix_content_items_is_mixed_roundup" not in indexes:
        op.create_index(
            "ix_content_items_is_mixed_roundup",
            "content_items",
            ["is_mixed_roundup"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_content_items_is_mixed_roundup", table_name="content_items")
    op.drop_index("ix_content_items_tech_relevance", table_name="content_items")
    op.drop_column("content_items", "is_mixed_roundup")
    op.drop_column("content_items", "tech_relevance_reason")
    op.drop_column("content_items", "tech_relevance_confidence")
    op.drop_column("content_items", "tech_relevance")
