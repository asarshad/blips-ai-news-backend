"""add editorial control fields and editorial_actions table

Revision ID: editorial_control_001
Revises: curation_system_002_drop_clusters
Create Date: 2026-02-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "editorial_control_001"
down_revision = "add_conversation_starters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extend content_items with editorial columns ───────────────────
    op.add_column(
        "content_items",
        sa.Column("editorial_boost", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "content_items",
        sa.Column("manual_added", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "content_items",
        sa.Column("added_by", sa.Text(), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("last_modified_by", sa.Text(), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("last_modified_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Indexes on editorial columns
    op.create_index("ix_content_items_editorial_boost", "content_items", ["editorial_boost"])

    # ── Create editorial_actions audit table ──────────────────────────
    op.create_table(
        "editorial_actions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "content_id",
            sa.BigInteger(),
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("old_value", postgresql.JSONB(), nullable=True),
        sa.Column("new_value", postgresql.JSONB(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_editorial_actions_content_created",
        "editorial_actions",
        ["content_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_editorial_actions_content_created", table_name="editorial_actions")
    op.drop_table("editorial_actions")

    op.drop_index("ix_content_items_editorial_boost", table_name="content_items")
    op.drop_column("content_items", "last_modified_at")
    op.drop_column("content_items", "last_modified_by")
    op.drop_column("content_items", "added_at")
    op.drop_column("content_items", "added_by")
    op.drop_column("content_items", "manual_added")
    op.drop_column("content_items", "editorial_boost")
