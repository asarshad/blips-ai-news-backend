"""Add curation_status, promotion fields, and signal_urls table

Revision ID: signal_coverage_001
Revises: add_language_column_001
Create Date: 2026-03-02

Implements the Coverage Guarantee + Quality Gate pipeline:

1. Adds ``contentstatus`` PostgreSQL enum (CANDIDATE | PROMOTED).
2. Adds new columns to ``content_items``:
   - curation_status  – pipeline tier for each item
   - discovered_via   – how this item entered the system
   - signal_hits      – count of signal sources referencing this URL
   - promotion_score  – score used by PromotionService
3. Creates ``signal_urls`` table with ``signalsource`` and ``enqueuedstatus``
   enums and a unique constraint on (canonical_url, signal_source).

SAFE DEFAULTS:
- Existing rows get curation_status = 'PROMOTED' (they are already in the feed).
- signal_hits defaults to 0; promotion_score is nullable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "signal_coverage_001"
down_revision = "add_language_column_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. contentstatus enum ─────────────────────────────────────────────
    row = conn.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'contentstatus'"))
    if not row.fetchone():
        op.execute("CREATE TYPE contentstatus AS ENUM ('CANDIDATE', 'PROMOTED')")

    # ── 2. New columns on content_items ───────────────────────────────────
    # curation_status:  existing rows go straight to PROMOTED (backward compat)
    op.add_column(
        "content_items",
        sa.Column(
            "curation_status",
            postgresql.ENUM("CANDIDATE", "PROMOTED", name="contentstatus", create_type=False),
            nullable=False,
            server_default="PROMOTED",
        ),
    )
    op.create_index(
        "ix_content_items_curation_status",
        "content_items",
        ["curation_status"],
    )

    op.add_column(
        "content_items",
        sa.Column("discovered_via", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_content_items_discovered_via",
        "content_items",
        ["discovered_via"],
    )

    op.add_column(
        "content_items",
        sa.Column("signal_hits", sa.Integer(), nullable=False, server_default="0"),
    )

    op.add_column(
        "content_items",
        sa.Column("promotion_score", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_content_items_promotion_score",
        "content_items",
        ["promotion_score"],
    )

    # Composite index for the promotion pipeline query
    op.create_index(
        "ix_content_curation_type_score",
        "content_items",
        ["curation_status", "type", "promotion_score"],
    )

    # ── 3. signalsource enum ──────────────────────────────────────────────
    row = conn.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'signalsource'"))
    if not row.fetchone():
        op.execute(
            "CREATE TYPE signalsource AS ENUM "
            "('hn_top', 'hn_best', 'github_trending', 'yt_trending')"
        )

    # ── 4. enqueuedstatus enum ────────────────────────────────────────────
    row = conn.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'enqueuedstatus'"))
    if not row.fetchone():
        op.execute(
            "CREATE TYPE enqueuedstatus AS ENUM ('pending', 'ingested', 'duplicate', 'rejected')"
        )

    # ── 5. signal_urls table ──────────────────────────────────────────────
    inspector = sa.inspect(conn)
    if "signal_urls" not in inspector.get_table_names():
        op.create_table(
            "signal_urls",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("raw_url", sa.Text(), nullable=False),
            sa.Column("canonical_url", sa.String(2048), nullable=False),
            sa.Column(
                "signal_source",
                postgresql.ENUM(
                    "hn_top",
                    "hn_best",
                    "github_trending",
                    "yt_trending",
                    name="signalsource",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("raw_title", sa.Text(), nullable=True),
            sa.Column("signal_score", sa.Integer(), nullable=True),
            sa.Column(
                "enqueue_status",
                postgresql.ENUM(
                    "pending",
                    "ingested",
                    "duplicate",
                    "rejected",
                    name="enqueuedstatus",
                    create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "content_item_id",
                sa.Integer(),
                sa.ForeignKey("content_items.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("hit_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("enqueued_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("canonical_url", "signal_source", name="uq_signal_url_source"),
        )
        op.create_index("ix_signal_urls_id", "signal_urls", ["id"])
        op.create_index("ix_signal_urls_canonical_url", "signal_urls", ["canonical_url"])
        op.create_index("ix_signal_urls_signal_source", "signal_urls", ["signal_source"])
        op.create_index("ix_signal_urls_enqueue_status", "signal_urls", ["enqueue_status"])
        op.create_index(
            "ix_signal_status_source",
            "signal_urls",
            ["enqueue_status", "signal_source"],
        )
        op.create_index(
            "ix_signal_urls_content_item_id",
            "signal_urls",
            ["content_item_id"],
        )


def downgrade() -> None:
    """Remove signal system additions.  DEVELOPMENT USE ONLY."""
    op.drop_table("signal_urls")

    op.drop_index("ix_content_curation_type_score", table_name="content_items")
    op.drop_index("ix_content_items_promotion_score", table_name="content_items")
    op.drop_index("ix_content_items_discovered_via", table_name="content_items")
    op.drop_index("ix_content_items_curation_status", table_name="content_items")

    op.drop_column("content_items", "promotion_score")
    op.drop_column("content_items", "signal_hits")
    op.drop_column("content_items", "discovered_via")
    op.drop_column("content_items", "curation_status")

    op.execute("DROP TYPE IF EXISTS enqueuedstatus")
    op.execute("DROP TYPE IF EXISTS signalsource")
    op.execute("DROP TYPE IF EXISTS contentstatus")
