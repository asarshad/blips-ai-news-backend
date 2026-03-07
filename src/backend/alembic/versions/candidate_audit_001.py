"""Add candidate audit table and provenance fields.

Revision ID: candidate_audit_001
Revises: add_user_category_selections
Create Date: 2026-03-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "candidate_audit_001"
down_revision = "add_user_category_selections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # --- candidate_audit_events table ---
    if "candidate_audit_events" not in inspector.get_table_names():
        op.create_table(
            "candidate_audit_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("content_item_id", sa.Integer(), nullable=True),
            sa.Column("canonical_url", sa.String(length=2048), nullable=False),
            sa.Column("signal_source", sa.String(length=64), nullable=False),
            sa.Column("discovered_via", sa.String(length=64), nullable=True),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
            sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_candidate_audit_events_id", "candidate_audit_events", ["id"])
        op.create_index(
            "ix_candidate_audit_events_content_item_id",
            "candidate_audit_events",
            ["content_item_id"],
        )
        op.create_index(
            "ix_candidate_audit_events_canonical_url",
            "candidate_audit_events",
            ["canonical_url"],
        )
        op.create_index(
            "ix_candidate_audit_events_signal_source",
            "candidate_audit_events",
            ["signal_source"],
        )
        op.create_index(
            "ix_candidate_audit_events_discovered_via",
            "candidate_audit_events",
            ["discovered_via"],
        )
        op.create_index(
            "ix_candidate_audit_events_event_type",
            "candidate_audit_events",
            ["event_type"],
        )
        op.create_index(
            "ix_candidate_audit_events_created_at",
            "candidate_audit_events",
            ["created_at"],
        )

    # --- content_items provenance columns ---
    content_cols = {col["name"] for col in inspector.get_columns("content_items")}

    if "candidate_first_seen_at" not in content_cols:
        op.add_column(
            "content_items",
            sa.Column("candidate_first_seen_at", sa.DateTime(), nullable=True),
        )
        op.create_index(
            "ix_content_items_candidate_first_seen_at",
            "content_items",
            ["candidate_first_seen_at"],
        )

    if "candidate_signal_source" not in content_cols:
        op.add_column(
            "content_items",
            sa.Column("candidate_signal_source", sa.String(length=64), nullable=True),
        )
        op.create_index(
            "ix_content_items_candidate_signal_source",
            "content_items",
            ["candidate_signal_source"],
        )

    if "candidate_raw_title" not in content_cols:
        op.add_column(
            "content_items",
            sa.Column("candidate_raw_title", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("content_items", "candidate_raw_title")

    op.drop_index("ix_content_items_candidate_signal_source", table_name="content_items")
    op.drop_column("content_items", "candidate_signal_source")

    op.drop_index("ix_content_items_candidate_first_seen_at", table_name="content_items")
    op.drop_column("content_items", "candidate_first_seen_at")

    op.drop_index("ix_candidate_audit_events_created_at", table_name="candidate_audit_events")
    op.drop_index("ix_candidate_audit_events_event_type", table_name="candidate_audit_events")
    op.drop_index("ix_candidate_audit_events_discovered_via", table_name="candidate_audit_events")
    op.drop_index("ix_candidate_audit_events_signal_source", table_name="candidate_audit_events")
    op.drop_index("ix_candidate_audit_events_canonical_url", table_name="candidate_audit_events")
    op.drop_index("ix_candidate_audit_events_content_item_id", table_name="candidate_audit_events")
    op.drop_index("ix_candidate_audit_events_id", table_name="candidate_audit_events")
    op.drop_table("candidate_audit_events")
