"""Add durable source fetch response state.

Revision ID: source_fetch_state_001
Revises: content_reports_001
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "source_fetch_state_001"
down_revision = "content_reports_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_fetch_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("feed_name", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="healthy"),
        sa.Column("last_action", sa.String(length=64), nullable=False, server_default="success"),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("redirect_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "consecutive_failure_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type",
            "feed_name",
            name="uq_source_fetch_states_source_feed",
        ),
    )
    op.create_index(
        "ix_source_fetch_states_source_type",
        "source_fetch_states",
        ["source_type"],
    )
    op.create_index("ix_source_fetch_states_feed_name", "source_fetch_states", ["feed_name"])
    op.create_index(
        "ix_source_fetch_states_health_status",
        "source_fetch_states",
        ["health_status"],
    )
    op.create_index("ix_source_fetch_states_last_action", "source_fetch_states", ["last_action"])
    op.create_index(
        "ix_source_fetch_states_cooldown_until",
        "source_fetch_states",
        ["cooldown_until"],
    )
    op.create_index(
        "ix_source_fetch_states_status_cooldown",
        "source_fetch_states",
        ["health_status", "cooldown_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_fetch_states_status_cooldown", table_name="source_fetch_states")
    op.drop_index("ix_source_fetch_states_cooldown_until", table_name="source_fetch_states")
    op.drop_index("ix_source_fetch_states_last_action", table_name="source_fetch_states")
    op.drop_index("ix_source_fetch_states_health_status", table_name="source_fetch_states")
    op.drop_index("ix_source_fetch_states_feed_name", table_name="source_fetch_states")
    op.drop_index("ix_source_fetch_states_source_type", table_name="source_fetch_states")
    op.drop_table("source_fetch_states")
