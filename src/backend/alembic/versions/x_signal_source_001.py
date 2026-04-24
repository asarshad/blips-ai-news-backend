"""Add x_signal value to signalsource enum.

X (Twitter) is used as a signal amplification layer only. Posts are never
ingested as content; only the URLs they contain are extracted and fed into
the existing signal pipeline.

Revision ID: x_signal_source_001
Revises: starter_answers_001
Create Date: 2026-04-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "x_signal_source_001"
down_revision = "starter_answers_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_type t
                    JOIN pg_enum e ON t.oid = e.enumtypid
                    WHERE t.typname = 'signalsource'
                      AND e.enumlabel = 'x_signal'
                ) THEN
                    ALTER TYPE signalsource ADD VALUE 'x_signal';
                END IF;
            END
            $$;
            """
        )
    )


def downgrade() -> None:
    # PostgreSQL enums do not support dropping values in-place safely.
    # Kept as no-op consistent with the additive migration policy used
    # throughout this project (see discovery_leads_signal_001.py).
    return
