"""Add article image verification state

Revision ID: article_image_verification_001
Revises: content_readiness_001
Create Date: 2026-03-25

MIGRATION POLICY: ADDITIVE ONLY
- Adds per-article image verification state used by readiness gating
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "article_image_verification_001"
down_revision = "content_readiness_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("content_items")}

    if "article_image_status" not in existing:
        op.add_column(
            "content_items",
            sa.Column("article_image_status", sa.String(length=32), nullable=True),
        )
    if "article_image_checked_at" not in existing:
        op.add_column(
            "content_items",
            sa.Column("article_image_checked_at", sa.DateTime(), nullable=True),
        )

    op.execute(
        """
        UPDATE content_items
        SET
            article_image_status = CASE
                WHEN type = 'ARTICLE' AND NULLIF(BTRIM(COALESCE(image_url, '')), '') IS NOT NULL
                    THEN 'VERIFIED'
                WHEN type = 'ARTICLE' THEN 'MISSING'
                ELSE article_image_status
            END,
            article_image_checked_at = CASE
                WHEN type = 'ARTICLE' THEN COALESCE(article_image_checked_at, NOW())
                ELSE article_image_checked_at
            END
        """
    )

    op.create_index(
        "ix_content_items_article_image_status",
        "content_items",
        ["article_image_status"],
    )
    op.create_index(
        "ix_content_items_article_image_checked_at",
        "content_items",
        ["article_image_checked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_items_article_image_checked_at", table_name="content_items")
    op.drop_index("ix_content_items_article_image_status", table_name="content_items")
    op.drop_column("content_items", "article_image_checked_at")
    op.drop_column("content_items", "article_image_status")
