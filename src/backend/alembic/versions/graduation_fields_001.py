"""Add status_changed_at and probation_until to video_source_profiles

Revision ID: graduation_fields_001
Revises: video_reels_overhaul_001
Create Date: 2026-03-20

MIGRATION POLICY: ADDITIVE ONLY
- Adds status_changed_at for anti-oscillation cooldown tracking
- Adds probation_until for post-graduation probation window
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "graduation_fields_001"
down_revision = "video_reels_overhaul_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "video_source_profiles" in inspector.get_table_names():
        existing = {c["name"] for c in inspector.get_columns("video_source_profiles")}

        if "status_changed_at" not in existing:
            op.add_column(
                "video_source_profiles",
                sa.Column("status_changed_at", sa.DateTime(), nullable=True),
            )

        if "probation_until" not in existing:
            op.add_column(
                "video_source_profiles",
                sa.Column("probation_until", sa.DateTime(), nullable=True),
            )


def downgrade() -> None:
    pass
