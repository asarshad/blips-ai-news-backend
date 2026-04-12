"""Add starter_answers column to content_items

Revision ID: starter_answers_001
Revises: simple_session_auth_001
Create Date: 2026-04-10

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "starter_answers_001"
down_revision = "simple_session_auth_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add starter_answers JSONB column to content_items."""
    op.add_column(
        "content_items",
        sa.Column(
            "starter_answers",
            JSONB,
            nullable=True,
            comment='AI-generated starter answers: {"Prompt": "Answer"}',
        ),
    )


def downgrade() -> None:
    """Remove starter_answers column."""
    op.drop_column("content_items", "starter_answers")
