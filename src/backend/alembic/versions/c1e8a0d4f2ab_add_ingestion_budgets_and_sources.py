"""add_ingestion_budgets_and_sources

Revision ID: c1e8a0d4f2ab
Revises: b7d1f4a2c9e0
Create Date: 2026-02-04

MIGRATION POLICY: ADDITIVE ONLY
- Adds ingestion_budgets for strict target caps (no overshoot)
- Adds sources table for governance (weights/caps)
- Adds source_daily_stats for per-day enforcement/observability
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c1e8a0d4f2ab"
down_revision = "b7d1f4a2c9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # sources governance table
    if "sources" not in inspector.get_table_names():
        op.create_table(
            "sources",
            sa.Column("name", sa.String(length=255), primary_key=True),
            sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("daily_cap", sa.Integer(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        )
        op.create_index("ix_sources_enabled", "sources", ["enabled"])
        op.create_index("ix_sources_weight", "sources", ["weight"])

    # Seed sources from existing content (best-effort)
    if "content_items" in inspector.get_table_names():
        conn.execute(
            sa.text(
                """
                INSERT INTO sources (name, weight, enabled, created_at, updated_at)
                SELECT DISTINCT source, 1.0, true, NOW(), NOW()
                FROM content_items
                WHERE source IS NOT NULL AND source != ''
                ON CONFLICT (name) DO NOTHING
                """
            )
        )

    # Daily stats for caps
    if "source_daily_stats" not in inspector.get_table_names():
        op.create_table(
            "source_daily_stats",
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("source", sa.String(length=255), nullable=False),
            sa.Column("inserted", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("suppressed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
            sa.PrimaryKeyConstraint("day", "source", name="pk_source_daily_stats"),
        )
        op.create_index("ix_source_daily_stats_day", "source_daily_stats", ["day"])

    # ingestion_budgets
    if "ingestion_budgets" not in inspector.get_table_names():
        contenttype_enum = postgresql.ENUM(
            "ARTICLE",
            "VIDEO",
            "REEL",
            name="contenttype",
            create_type=False,
        )
        op.create_table(
            "ingestion_budgets",
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("content_type", contenttype_enum, nullable=False),
            sa.Column("target", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("inserted", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("seen", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("suppressed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
            sa.PrimaryKeyConstraint("day", "content_type", name="pk_ingestion_budgets"),
        )
        op.create_index("ix_ingestion_budgets_day", "ingestion_budgets", ["day"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_budgets_day", table_name="ingestion_budgets")
    op.drop_table("ingestion_budgets")

    op.drop_index("ix_source_daily_stats_day", table_name="source_daily_stats")
    op.drop_table("source_daily_stats")

    op.drop_index("ix_sources_weight", table_name="sources")
    op.drop_index("ix_sources_enabled", table_name="sources")
    op.drop_table("sources")
