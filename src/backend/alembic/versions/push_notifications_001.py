"""Add push notification subscriptions and send logs

Revision ID: push_notifications_001
Revises: graduation_fields_002
Create Date: 2026-03-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "push_notifications_001"
down_revision = "graduation_fields_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "device_id",
            sa.String(length=255),
            sa.ForeignKey("user_profiles.device_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("device_id", "token", name="uq_push_subscriptions_device_token"),
    )
    op.create_index("ix_push_subscriptions_device_id", "push_subscriptions", ["device_id"])
    op.create_index("ix_push_subscriptions_token", "push_subscriptions", ["token"])
    op.create_index("ix_push_subscriptions_active", "push_subscriptions", ["active"])
    op.create_index(
        "ix_push_subscriptions_active_last_seen",
        "push_subscriptions",
        ["active", "last_seen_at"],
    )

    op.create_table(
        "push_send_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "content_item_id",
            sa.Integer(),
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audience_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid_token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auto_dedup_key", sa.String(length=255), nullable=True, unique=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_push_send_logs_content_item_id", "push_send_logs", ["content_item_id"])
    op.create_index("ix_push_send_logs_mode", "push_send_logs", ["mode"])
    op.create_index("ix_push_send_logs_created_at", "push_send_logs", ["created_at"])
    op.create_index(
        "ix_push_send_logs_content_created",
        "push_send_logs",
        ["content_item_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_push_send_logs_content_created", table_name="push_send_logs")
    op.drop_index("ix_push_send_logs_created_at", table_name="push_send_logs")
    op.drop_index("ix_push_send_logs_mode", table_name="push_send_logs")
    op.drop_index("ix_push_send_logs_content_item_id", table_name="push_send_logs")
    op.drop_table("push_send_logs")

    op.drop_index("ix_push_subscriptions_active_last_seen", table_name="push_subscriptions")
    op.drop_index("ix_push_subscriptions_active", table_name="push_subscriptions")
    op.drop_index("ix_push_subscriptions_token", table_name="push_subscriptions")
    op.drop_index("ix_push_subscriptions_device_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
