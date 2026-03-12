"""Video/reels overhaul schema.

Revision ID: video_reels_overhaul_001
Revises: cb1f06a7d6d5
Create Date: 2026-03-12 13:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "video_reels_overhaul_001"
down_revision: Union[str, Sequence[str], None] = "cb1f06a7d6d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_EVENT_TYPES = (
    "VIDEO_IMPRESSION",
    "VIDEO_START",
    "VIDEO_3S",
    "VIDEO_50PCT",
    "VIDEO_95PCT",
    "VIDEO_SKIP_LT_2S",
    "VIDEO_SAVE",
    "VIDEO_SHARE",
    "LESS_FROM_CREATOR",
    "CAUGHT_UP",
)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        for value in _NEW_EVENT_TYPES:
            op.execute(sa.text(f"ALTER TYPE eventtype ADD VALUE IF NOT EXISTS '{value}'"))

    op.add_column("content_items", sa.Column("channel_id", sa.String(length=64), nullable=True))
    op.add_column(
        "content_items",
        sa.Column("promotion_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("acquisition_lane", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("source_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("view_count_snapshot", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column(
            "engagement_snapshot",
            postgresql.JSONB(astext_type=sa.Text()) if dialect == "postgresql" else sa.JSON(),
            nullable=True,
        ),
    )
    op.add_column("content_items", sa.Column("views_per_hour", sa.Float(), nullable=True))
    op.add_column("content_items", sa.Column("format_fit_score", sa.Float(), nullable=True))

    op.create_index("ix_content_items_channel_id", "content_items", ["channel_id"], unique=False)
    op.create_index(
        "ix_content_items_acquisition_lane",
        "content_items",
        ["acquisition_lane"],
        unique=False,
    )
    op.create_index(
        "ix_content_items_source_status",
        "content_items",
        ["source_status"],
        unique=False,
    )
    op.create_index(
        "ix_content_items_views_per_hour",
        "content_items",
        ["views_per_hour"],
        unique=False,
    )

    op.create_table(
        "video_source_profiles",
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("channel_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("content_format", sa.String(length=32), nullable=False),
        sa.Column("quality_tier", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="discovery"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("curated_seed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("daily_video_cap", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("daily_reel_cap", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("allow_curated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allow_search", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allow_trending", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("score_7d", sa.Float(), nullable=False, server_default="0"),
        sa.Column("promotion_rate_7d", sa.Float(), nullable=False, server_default="0"),
        sa.Column("suppression_rate_7d", sa.Float(), nullable=False, server_default="0"),
        sa.Column("clickbait_rate_7d", sa.Float(), nullable=False, server_default="0"),
        sa.Column("freshness_yield_7d", sa.Float(), nullable=False, server_default="0"),
        sa.Column("post_start_consumption_7d", sa.Float(), nullable=False, server_default="0"),
        sa.Column("promoted_share_7d", sa.Float(), nullable=False, server_default="0"),
        sa.Column("early_skip_rate_7d", sa.Float(), nullable=False, server_default="0"),
        sa.Column("completion_rate_7d", sa.Float(), nullable=False, server_default="0"),
        sa.Column("save_share_rate_7d", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_promoted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("channel_id"),
    )
    op.create_index(
        "ix_video_source_profiles_channel_name",
        "video_source_profiles",
        ["channel_name"],
        unique=False,
    )
    op.create_index(
        "ix_video_source_profiles_role",
        "video_source_profiles",
        ["role"],
        unique=False,
    )
    op.create_index(
        "ix_video_source_profiles_status",
        "video_source_profiles",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_video_source_profiles_enabled",
        "video_source_profiles",
        ["enabled"],
        unique=False,
    )
    op.create_index(
        "ix_video_source_profiles_last_seen_at",
        "video_source_profiles",
        ["last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_video_source_profiles_last_promoted_at",
        "video_source_profiles",
        ["last_promoted_at"],
        unique=False,
    )
    op.create_index(
        "ix_video_source_status_enabled",
        "video_source_profiles",
        ["status", "enabled"],
        unique=False,
    )
    op.create_index(
        "ix_video_source_role_status",
        "video_source_profiles",
        ["role", "status"],
        unique=False,
    )

    op.create_table(
        "video_discovery_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lane", sa.String(length=32), nullable=False),
        sa.Column("surface", sa.String(length=16), nullable=False),
        sa.Column("query_label", sa.String(length=128), nullable=True),
        sa.Column("region", sa.String(length=8), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("promoted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "distinct_promoted_channels",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("median_promoted_age_hours", sa.Float(), nullable=True),
        sa.Column("duplicate_rejections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clickbait_rejections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filtered_non_english", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filtered_live", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filtered_off_topic", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filtered_format", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "run_started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_video_discovery_runs_lane",
        "video_discovery_runs",
        ["lane"],
        unique=False,
    )
    op.create_index(
        "ix_video_discovery_runs_surface",
        "video_discovery_runs",
        ["surface"],
        unique=False,
    )
    op.create_index(
        "ix_video_discovery_runs_query_label",
        "video_discovery_runs",
        ["query_label"],
        unique=False,
    )
    op.create_index(
        "ix_video_discovery_runs_region",
        "video_discovery_runs",
        ["region"],
        unique=False,
    )
    op.create_index(
        "ix_video_discovery_runs_run_started_at",
        "video_discovery_runs",
        ["run_started_at"],
        unique=False,
    )
    op.create_index(
        "ix_video_discovery_lane_surface_started",
        "video_discovery_runs",
        ["lane", "surface", "run_started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_video_discovery_lane_surface_started", table_name="video_discovery_runs")
    op.drop_index("ix_video_discovery_runs_run_started_at", table_name="video_discovery_runs")
    op.drop_index("ix_video_discovery_runs_region", table_name="video_discovery_runs")
    op.drop_index("ix_video_discovery_runs_query_label", table_name="video_discovery_runs")
    op.drop_index("ix_video_discovery_runs_surface", table_name="video_discovery_runs")
    op.drop_index("ix_video_discovery_runs_lane", table_name="video_discovery_runs")
    op.drop_table("video_discovery_runs")

    op.drop_index("ix_video_source_role_status", table_name="video_source_profiles")
    op.drop_index("ix_video_source_status_enabled", table_name="video_source_profiles")
    op.drop_index("ix_video_source_profiles_last_promoted_at", table_name="video_source_profiles")
    op.drop_index("ix_video_source_profiles_last_seen_at", table_name="video_source_profiles")
    op.drop_index("ix_video_source_profiles_enabled", table_name="video_source_profiles")
    op.drop_index("ix_video_source_profiles_status", table_name="video_source_profiles")
    op.drop_index("ix_video_source_profiles_role", table_name="video_source_profiles")
    op.drop_index("ix_video_source_profiles_channel_name", table_name="video_source_profiles")
    op.drop_table("video_source_profiles")

    op.drop_index("ix_content_items_views_per_hour", table_name="content_items")
    op.drop_index("ix_content_items_source_status", table_name="content_items")
    op.drop_index("ix_content_items_acquisition_lane", table_name="content_items")
    op.drop_index("ix_content_items_channel_id", table_name="content_items")

    op.drop_column("content_items", "format_fit_score")
    op.drop_column("content_items", "views_per_hour")
    op.drop_column("content_items", "engagement_snapshot")
    op.drop_column("content_items", "view_count_snapshot")
    op.drop_column("content_items", "source_status")
    op.drop_column("content_items", "acquisition_lane")
    op.drop_column("content_items", "promotion_reason")
    op.drop_column("content_items", "channel_id")

    # PostgreSQL enum values are intentionally left in place on downgrade.
