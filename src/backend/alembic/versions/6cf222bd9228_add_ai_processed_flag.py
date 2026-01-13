
"""add_ai_processed_flag

Revision ID: 6cf222bd9228
Revises: 72112eec4950
Create Date: 2025-12-25 08:43:38.133579

MIGRATION POLICY: ADDITIVE ONLY
- Adds ai_processed flag to content_items if table exists
- Safe to run on databases with or without curation system
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '6cf222bd9228'
down_revision = '72112eec4950'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if content_items table exists
    conn = op.get_bind()
    inspector = inspect(conn)
    
    if 'content_items' not in inspector.get_table_names():
        # Skip this migration if content_items doesn't exist
        # (user hasn't migrated to curation system yet)
        return
    
    # Check if ai_processed column already exists
    columns = [col['name'] for col in inspector.get_columns('content_items')]
    if 'ai_processed' in columns:
        # Column already exists, skip
        return
    
    # Add ai_processed column to content_items
    op.add_column(
        'content_items',
        sa.Column('ai_processed', sa.Boolean(), nullable=False, server_default='false')
    )
    
    # Create index for querying AI-processed content
    op.create_index('ix_content_items_ai_processed', 'content_items', ['ai_processed'])
    
    # Update existing content that has summaries to be marked as processed
    # Videos/REELs without summaries should stay False
    op.execute("""
        UPDATE content_items 
        SET ai_processed = TRUE 
        WHERE summary IS NOT NULL AND summary != '' AND type = 'ARTICLE'
    """)
    
    op.execute("""
        UPDATE content_items 
        SET ai_processed = TRUE 
        WHERE summary IS NOT NULL AND summary != '' AND type = 'VIDEO' 
        AND NOT summary LIKE 'Watch this video%'
    """)


def downgrade() -> None:
    op.drop_index('ix_content_items_ai_processed', table_name='content_items')
    op.drop_column('content_items', 'ai_processed')
