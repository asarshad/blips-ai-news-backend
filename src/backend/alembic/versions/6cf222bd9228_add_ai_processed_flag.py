
"""add_ai_processed_flag

Revision ID: 6cf222bd9228
Revises: 72112eec4950
Create Date: 2025-12-25 08:43:38.133579

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6cf222bd9228'
down_revision = '72112eec4950'
branch_labels = None
depends_on = None


def upgrade() -> None:
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
