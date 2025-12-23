"""Drop content_clusters table - clusters now implicit via cluster_id

Revision ID: curation_system_002
Revises: curation_system_001
Create Date: 2024-12-23 00:00:00.000000

This migration removes the content_clusters table.
Clusters are now implicit via the cluster_id field on content_items.
Cluster metadata is computed on-the-fly via aggregation queries.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'curation_system_002'
down_revision = 'curation_system_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop content_clusters table indexes
    op.drop_index('ix_content_clusters_primary_topic', table_name='content_clusters')
    op.drop_index('ix_content_clusters_window_start', table_name='content_clusters')
    op.drop_index('ix_content_clusters_window_end', table_name='content_clusters')
    
    # Drop content_clusters table
    op.drop_table('content_clusters')


def downgrade() -> None:
    # Recreate content_clusters table
    op.create_table(
        'content_clusters',
        sa.Column('id', sa.String(64), nullable=False),
        sa.Column('primary_topic', sa.String(255), nullable=True),
        sa.Column('primary_entities', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('item_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('first_seen', sa.DateTime(), nullable=False),
        sa.Column('last_updated', sa.DateTime(), nullable=False),
        sa.Column('window_start', sa.DateTime(), nullable=False),
        sa.Column('window_end', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_content_clusters_primary_topic', 'content_clusters', ['primary_topic'])
    op.create_index('ix_content_clusters_window_start', 'content_clusters', ['window_start'])
    op.create_index('ix_content_clusters_window_end', 'content_clusters', ['window_end'])
