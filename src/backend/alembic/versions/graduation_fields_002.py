"""Add low_promotion_since to video_source_profiles

Revision ID: graduation_fields_002
Revises: graduation_fields_001
Create Date: 2026-03-20

MIGRATION POLICY: ADDITIVE ONLY
- Adds low_promotion_since for sustained-demotion tracking
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "graduation_fields_002"
down_revision = "graduation_fields_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "video_source_profiles" in inspector.get_table_names():
        existing = {c["name"] for c in inspector.get_columns("video_source_profiles")}
        if "low_promotion_since" not in existing:
            op.add_column(
                "video_source_profiles",
                sa.Column("low_promotion_since", sa.DateTime(), nullable=True),
            )


def downgrade() -> None:
    pass
