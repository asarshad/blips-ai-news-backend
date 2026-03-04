"""add user_category_selections table

Revision ID: add_user_category_selections
Revises: signal_coverage_001
Create Date: 2026-03-04

Adds an explicit user-declared category interest table separate from the
learned user_preferences table.  The selected_categories column is a JSONB
array of strings (e.g. ["AI", "Security"]) set during onboarding or settings.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "add_user_category_selections"
down_revision = "signal_coverage_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_category_selections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(255), nullable=False),
        sa.Column(
            "selected_categories",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["user_profiles.device_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", name="uq_user_category_selections_device_id"),
    )
    op.create_index(
        "ix_user_category_selections_device_id",
        "user_category_selections",
        ["device_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_category_selections_device_id",
        table_name="user_category_selections",
    )
    op.drop_table("user_category_selections")
