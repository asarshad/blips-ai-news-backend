"""merge candidate_audit and discovery leads heads

Revision ID: cb1f06a7d6d5
Revises: candidate_audit_001, discovery_leads_signal_001
Create Date: 2026-03-07 02:01:53.188132

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "cb1f06a7d6d5"
down_revision = ("candidate_audit_001", "discovery_leads_signal_001")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
