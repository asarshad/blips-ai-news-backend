"""Add anonymous bearer session table.

Revision ID: simple_session_auth_001
Revises: cb1f06a7d6d5, video_tech_classifier_001
Create Date: 2026-03-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "simple_session_auth_001"
down_revision = ("cb1f06a7d6d5", "video_tech_classifier_001")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_sessions",
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("app_version", sa.String(length=64), nullable=True),
        sa.Column("refresh_token_hash", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["user_profiles.device_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_index("ix_device_sessions_platform", "device_sessions", ["platform"])
    op.create_index(
        "ix_device_sessions_refresh_token_hash",
        "device_sessions",
        ["refresh_token_hash"],
    )
    op.create_index("ix_device_sessions_expires_at", "device_sessions", ["expires_at"])
    op.create_index("ix_device_sessions_last_seen_at", "device_sessions", ["last_seen_at"])
    op.create_index("ix_device_sessions_revoked_at", "device_sessions", ["revoked_at"])
    op.create_index(
        "ix_device_sessions_active_expiry",
        "device_sessions",
        ["revoked_at", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_sessions_active_expiry", table_name="device_sessions")
    op.drop_index("ix_device_sessions_revoked_at", table_name="device_sessions")
    op.drop_index("ix_device_sessions_last_seen_at", table_name="device_sessions")
    op.drop_index("ix_device_sessions_expires_at", table_name="device_sessions")
    op.drop_index("ix_device_sessions_refresh_token_hash", table_name="device_sessions")
    op.drop_index("ix_device_sessions_platform", table_name="device_sessions")
    op.drop_table("device_sessions")
