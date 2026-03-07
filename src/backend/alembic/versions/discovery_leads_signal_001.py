"""Add discovery_leads value to signalsource enum.

Revision ID: discovery_leads_signal_001
Revises: add_user_category_selections
Create Date: 2026-03-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "discovery_leads_signal_001"
down_revision = "add_user_category_selections"
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
                      AND e.enumlabel = 'discovery_leads'
                ) THEN
                    ALTER TYPE signalsource ADD VALUE 'discovery_leads';
                END IF;
            END
            $$;
            """
        )
    )


def downgrade() -> None:
    # PostgreSQL enums do not support dropping values in-place safely.
    # Kept as no-op for additive migration policy.
    return
