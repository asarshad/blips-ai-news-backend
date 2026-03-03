"""add language column to content_items

Revision ID: add_language_column_001
Revises: editorial_control_001
Create Date: 2026-03-02

Adds a nullable language column (ISO 639-1 code, e.g. "en", "es") to
content_items so that the ingestion pipeline can persist the detected
language and feed queries can filter to English-only content.

Rows inserted before this migration have language = NULL; the application
treats NULL as "en" for feed queries (see _ENGLISH_FILTER in content_repo.py).
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "add_language_column_001"
down_revision = "editorial_control_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column("language", sa.String(10), nullable=True),
    )
    op.create_index(
        "ix_content_items_language",
        "content_items",
        ["language"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_items_language", table_name="content_items")
    op.drop_column("content_items", "language")
