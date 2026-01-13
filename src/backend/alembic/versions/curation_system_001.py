"""Add curation system tables

Revision ID: curation_system_001
Revises: c539d69b3ccb
Create Date: 2024-12-22 00:00:00.000000

MIGRATION POLICY: ADDITIVE ONLY
This migration adds the unified content curation system:
- content_items: Unified table for articles, videos, and reels
- content_clusters: Story clustering for deduplication
- user_profiles: Device-based user profiles
- user_preferences: Learned interest weights
- interaction_events: Raw interaction signals

WARNING: Downgrade function is for development only.
Never run downgrade in production - it will destroy user data.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'curation_system_001'
down_revision = 'c539d69b3ccb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types
    op.execute("CREATE TYPE contenttype AS ENUM ('ARTICLE', 'VIDEO', 'REEL')")
    op.execute("CREATE TYPE preftype AS ENUM ('TOPIC', 'ENTITY', 'SOURCE', 'FORMAT')")
    op.execute("CREATE TYPE eventtype AS ENUM ('VIEW_10S', 'OPEN_SOURCE', 'SHARE', 'SAVE', 'CHAT_START', 'CHAT_MESSAGE')")
    
    # Create content_items table
    op.create_table(
        'content_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('type', sa.Enum('ARTICLE', 'VIDEO', 'REEL', name='contenttype', create_type=False), nullable=False),
        sa.Column('source', sa.String(255), nullable=False),
        sa.Column('source_url', sa.String(2048), nullable=False),
        sa.Column('canonical_url', sa.String(2048), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=False),
        sa.Column('title', sa.String(1024), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('image_url', sa.String(2048), nullable=True),
        sa.Column('video_url', sa.String(2048), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('topics', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('entities', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('quality_score', sa.Float(), nullable=True, server_default='0.5'),
        sa.Column('trend_score', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('recency_score', sa.Float(), nullable=True, server_default='1.0'),
        sa.Column('diversity_boost', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('global_score', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('cluster_id', sa.String(64), nullable=True),
        sa.Column('is_cluster_canonical', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('dedupe_key', sa.String(128), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for content_items
    op.create_index('ix_content_items_id', 'content_items', ['id'])
    op.create_index('ix_content_items_type', 'content_items', ['type'])
    op.create_index('ix_content_items_source', 'content_items', ['source'])
    op.create_index('ix_content_items_source_url', 'content_items', ['source_url'], unique=True)
    op.create_index('ix_content_items_published_at', 'content_items', ['published_at'])
    op.create_index('ix_content_items_title', 'content_items', ['title'])
    op.create_index('ix_content_items_quality_score', 'content_items', ['quality_score'])
    op.create_index('ix_content_items_trend_score', 'content_items', ['trend_score'])
    op.create_index('ix_content_items_recency_score', 'content_items', ['recency_score'])
    op.create_index('ix_content_items_global_score', 'content_items', ['global_score'])
    op.create_index('ix_content_items_cluster_id', 'content_items', ['cluster_id'])
    op.create_index('ix_content_items_dedupe_key', 'content_items', ['dedupe_key'])
    # Composite indexes for feed queries
    op.create_index('ix_content_type_published_score', 'content_items', ['type', 'published_at', 'global_score'])
    op.create_index('ix_content_cluster_type', 'content_items', ['cluster_id', 'type'])
    op.create_index('ix_content_type_score', 'content_items', ['type', 'global_score'])
    
    # Create content_clusters table
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
    
    # Create user_profiles table
    op.create_table(
        'user_profiles',
        sa.Column('device_id', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('device_id')
    )
    
    # Create user_preferences table
    op.create_table(
        'user_preferences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.String(255), nullable=False),
        sa.Column('pref_type', sa.Enum('TOPIC', 'ENTITY', 'SOURCE', 'FORMAT', name='preftype', create_type=False), nullable=False),
        sa.Column('key', sa.String(255), nullable=False),
        sa.Column('weight', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['device_id'], ['user_profiles.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_preferences_id', 'user_preferences', ['id'])
    op.create_index('ix_user_preferences_device_id', 'user_preferences', ['device_id'])
    op.create_index('ix_pref_device_type', 'user_preferences', ['device_id', 'pref_type'])
    op.create_unique_constraint('uq_user_pref', 'user_preferences', ['device_id', 'pref_type', 'key'])
    
    # Create interaction_events table
    op.create_table(
        'interaction_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.String(255), nullable=False),
        sa.Column('content_item_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.Enum('VIEW_10S', 'OPEN_SOURCE', 'SHARE', 'SAVE', 'CHAT_START', 'CHAT_MESSAGE', name='eventtype', create_type=False), nullable=False),
        sa.Column('event_value', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['device_id'], ['user_profiles.device_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['content_item_id'], ['content_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_interaction_events_id', 'interaction_events', ['id'])
    op.create_index('ix_interaction_events_device_id', 'interaction_events', ['device_id'])
    op.create_index('ix_interaction_events_content_item_id', 'interaction_events', ['content_item_id'])
    op.create_index('ix_interaction_events_created_at', 'interaction_events', ['created_at'])
    op.create_index('ix_interaction_device_created', 'interaction_events', ['device_id', 'created_at'])
    op.create_index('ix_interaction_content_created', 'interaction_events', ['content_item_id', 'created_at'])


def downgrade() -> None:
    # Drop tables in reverse order (respecting foreign keys)
    op.drop_table('interaction_events')
    op.drop_table('user_preferences')
    op.drop_table('user_profiles')
    op.drop_table('content_clusters')
    op.drop_table('content_items')
    
    # Drop enum types
    op.execute("DROP TYPE IF EXISTS eventtype")
    op.execute("DROP TYPE IF EXISTS preftype")
    op.execute("DROP TYPE IF EXISTS contenttype")
