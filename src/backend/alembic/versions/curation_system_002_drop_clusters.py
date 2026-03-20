"""Deprecate content_clusters table - clusters now implicit via cluster_id

Revision ID: curation_system_002
Revises: curation_system_001
Create Date: 2024-12-23 00:00:00.000000

This migration was originally designed to drop the content_clusters table.
However, to follow ADDITIVE-ONLY migration policy, we now keep the table
but mark it as deprecated. Clusters are computed on-the-fly via aggregation.

MIGRATION POLICY: ADDITIVE ONLY
- Never drop tables in production
- Never drop columns with data
- Deprecated tables can be cleaned up manually after verification
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "curation_system_002"
down_revision = "curation_system_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MIGRATION POLICY: ADDITIVE ONLY
    # The content_clusters table is now deprecated but NOT dropped.
    # Cluster metadata is computed on-the-fly via aggregation queries.
    # The table will be cleaned up manually after verifying no dependencies.
    #
    # Original destructive operations (DISABLED):
    # op.drop_index('ix_content_clusters_primary_topic', table_name='content_clusters')
    # op.drop_index('ix_content_clusters_window_start', table_name='content_clusters')
    # op.drop_index('ix_content_clusters_window_end', table_name='content_clusters')
    # op.drop_table('content_clusters')
    pass


def downgrade() -> None:
    # Recreate content_clusters table
    op.create_table(
        "content_clusters",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("primary_topic", sa.String(255), nullable=True),
        sa.Column(
            "primary_entities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("item_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("window_end", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_content_clusters_primary_topic", "content_clusters", ["primary_topic"])
    op.create_index("ix_content_clusters_window_start", "content_clusters", ["window_start"])
    op.create_index("ix_content_clusters_window_end", "content_clusters", ["window_end"])
