"""Add conversation_starters column to content_items

Revision ID: add_conversation_starters
Revises: curation_system_002
Create Date: 2026-02-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = 'add_conversation_starters'
down_revision = 'curation_system_002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add conversation_starters JSONB column to content_items."""
    op.add_column(
        'content_items',
        sa.Column(
            'conversation_starters',
            JSONB,
            nullable=True,
            comment='AI-generated conversation starters: {"starters": [...], "fallback": [...]}'
        )
    )


def downgrade() -> None:
    """Remove conversation_starters column."""
    op.drop_column('content_items', 'conversation_starters')
