"""Add content readiness state and lifecycle outbox

Revision ID: content_readiness_001
Revises: push_notifications_001
Create Date: 2026-03-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "content_readiness_001"
down_revision = "push_notifications_001"
branch_labels = None
depends_on = None


_READY_PREDICATE = """
NOT is_suppressed
AND curation_status = 'PROMOTED'
AND (promotion_reason IS NULL OR promotion_reason NOT LIKE '%|blocked=%')
AND (
    (
        type = 'ARTICLE'
        AND COALESCE(NULLIF(BTRIM(source_url), ''), NULLIF(BTRIM(canonical_url), '')) IS NOT NULL
        AND ai_processed = true
        AND NULLIF(BTRIM(COALESCE(summary, '')), '') IS NOT NULL
    )
    OR (
        type <> 'ARTICLE'
        AND NULLIF(BTRIM(COALESCE(title, '')), '') IS NOT NULL
        AND COALESCE(NULLIF(BTRIM(video_url), ''), NULLIF(BTRIM(source_url), '')) IS NOT NULL
    )
)
"""


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column(
            "readiness_status",
            sa.String(length=32),
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.add_column(
        "content_items",
        sa.Column("readiness_reason", sa.String(length=64), nullable=True),
    )
    op.add_column("content_items", sa.Column("ready_at", sa.DateTime(), nullable=True))
    op.add_column(
        "content_items",
        sa.Column(
            "readiness_updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.execute(
        f"""
        UPDATE content_items
        SET
            readiness_status = CASE
                WHEN {_READY_PREDICATE} THEN 'READY'
                ELSE 'PENDING'
            END,
            readiness_reason = CASE
                WHEN {_READY_PREDICATE} THEN
                    CASE
                        WHEN type = 'ARTICLE' THEN 'article_ready'
                        WHEN type = 'REEL' THEN 'reel_ready'
                        ELSE 'video_ready'
                    END
                WHEN is_suppressed THEN 'suppressed'
                WHEN curation_status <> 'PROMOTED' THEN 'awaiting_promotion'
                WHEN promotion_reason LIKE '%|blocked=%' THEN 'blocked_promotion'
                WHEN type = 'ARTICLE'
                    AND COALESCE(NULLIF(BTRIM(source_url), ''), NULLIF(BTRIM(canonical_url), '')) IS NULL
                    THEN 'missing_article_source'
                WHEN type = 'ARTICLE' AND ai_processed = false THEN 'awaiting_ai_processing'
                WHEN type = 'ARTICLE' THEN 'missing_article_summary'
                WHEN NULLIF(BTRIM(COALESCE(title, '')), '') IS NULL THEN 'missing_video_title'
                ELSE 'missing_video_url'
            END,
            ready_at = CASE
                WHEN {_READY_PREDICATE} THEN COALESCE(created_at, published_at, now())
                ELSE NULL
            END,
            readiness_updated_at = now()
        """
    )

    op.create_index("ix_content_items_readiness_status", "content_items", ["readiness_status"])
    op.create_index("ix_content_items_ready_at", "content_items", ["ready_at"])
    op.create_index(
        "ix_content_items_readiness_updated_at",
        "content_items",
        ["readiness_updated_at"],
    )
    op.create_index(
        "ix_content_readiness_type_published",
        "content_items",
        ["readiness_status", "type", "published_at"],
    )

    op.create_table(
        "content_event_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "content_item_id",
            sa.Integer(),
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_content_event_outbox_content_item_id",
        "content_event_outbox",
        ["content_item_id"],
    )
    op.create_index("ix_content_event_outbox_event_type", "content_event_outbox", ["event_type"])
    op.create_index(
        "ix_content_event_outbox_available_at",
        "content_event_outbox",
        ["available_at"],
    )
    op.create_index("ix_content_event_outbox_locked_at", "content_event_outbox", ["locked_at"])
    op.create_index(
        "ix_content_event_outbox_processed_at",
        "content_event_outbox",
        ["processed_at"],
    )
    op.create_index(
        "ix_content_event_outbox_created_at",
        "content_event_outbox",
        ["created_at"],
    )
    op.create_index(
        "ix_content_event_outbox_status_available",
        "content_event_outbox",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_content_event_outbox_content_created",
        "content_event_outbox",
        ["content_item_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_event_outbox_content_created", table_name="content_event_outbox")
    op.drop_index("ix_content_event_outbox_status_available", table_name="content_event_outbox")
    op.drop_index("ix_content_event_outbox_created_at", table_name="content_event_outbox")
    op.drop_index("ix_content_event_outbox_processed_at", table_name="content_event_outbox")
    op.drop_index("ix_content_event_outbox_locked_at", table_name="content_event_outbox")
    op.drop_index("ix_content_event_outbox_available_at", table_name="content_event_outbox")
    op.drop_index("ix_content_event_outbox_event_type", table_name="content_event_outbox")
    op.drop_index("ix_content_event_outbox_content_item_id", table_name="content_event_outbox")
    op.drop_table("content_event_outbox")

    op.drop_index("ix_content_readiness_type_published", table_name="content_items")
    op.drop_index("ix_content_items_readiness_updated_at", table_name="content_items")
    op.drop_index("ix_content_items_ready_at", table_name="content_items")
    op.drop_index("ix_content_items_readiness_status", table_name="content_items")
    op.drop_column("content_items", "readiness_updated_at")
    op.drop_column("content_items", "ready_at")
    op.drop_column("content_items", "readiness_reason")
    op.drop_column("content_items", "readiness_status")
