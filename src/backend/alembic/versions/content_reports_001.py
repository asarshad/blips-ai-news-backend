"""Add content_reports table for user-submitted moderation reports.

Revision ID: content_reports_001
Revises: x_signal_source_001
Create Date: 2026-04-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "content_reports_001"
down_revision = "x_signal_source_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("content_item_id", sa.Integer(), nullable=False),
        sa.Column("surface", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["user_profiles.device_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_content_reports_id", "content_reports", ["id"])
    op.create_index("ix_content_reports_device_id", "content_reports", ["device_id"])
    op.create_index("ix_content_reports_content_item_id", "content_reports", ["content_item_id"])
    op.create_index("ix_content_reports_created_at", "content_reports", ["created_at"])
    op.create_index(
        "ix_content_reports_device_created",
        "content_reports",
        ["device_id", "created_at"],
    )
    op.create_index(
        "ix_content_reports_reviewed",
        "content_reports",
        ["reviewed", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_reports_reviewed", table_name="content_reports")
    op.drop_index("ix_content_reports_device_created", table_name="content_reports")
    op.drop_index("ix_content_reports_created_at", table_name="content_reports")
    op.drop_index("ix_content_reports_content_item_id", table_name="content_reports")
    op.drop_index("ix_content_reports_device_id", table_name="content_reports")
    op.drop_index("ix_content_reports_id", table_name="content_reports")
    op.drop_table("content_reports")
