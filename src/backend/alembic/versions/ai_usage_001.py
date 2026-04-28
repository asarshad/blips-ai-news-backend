"""Add AI usage telemetry

Revision ID: ai_usage_001
Revises: x_signal_source_001
Create Date: 2026-04-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ai_usage_001"
down_revision = "x_signal_source_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("use_case", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), server_default="0", nullable=False),
        sa.Column("response_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_usage_id"), "ai_usage", ["id"], unique=False)
    op.create_index(op.f("ix_ai_usage_provider"), "ai_usage", ["provider"], unique=False)
    op.create_index(op.f("ix_ai_usage_model"), "ai_usage", ["model"], unique=False)
    op.create_index(op.f("ix_ai_usage_use_case"), "ai_usage", ["use_case"], unique=False)
    op.create_index(op.f("ix_ai_usage_status"), "ai_usage", ["status"], unique=False)
    op.create_index(op.f("ix_ai_usage_error_code"), "ai_usage", ["error_code"], unique=False)
    op.create_index(op.f("ix_ai_usage_created_at"), "ai_usage", ["created_at"], unique=False)
    op.create_index("ix_ai_usage_created_use_case", "ai_usage", ["created_at", "use_case"])
    op.create_index("ix_ai_usage_created_model", "ai_usage", ["created_at", "model"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_created_model", table_name="ai_usage")
    op.drop_index("ix_ai_usage_created_use_case", table_name="ai_usage")
    op.drop_index(op.f("ix_ai_usage_created_at"), table_name="ai_usage")
    op.drop_index(op.f("ix_ai_usage_error_code"), table_name="ai_usage")
    op.drop_index(op.f("ix_ai_usage_status"), table_name="ai_usage")
    op.drop_index(op.f("ix_ai_usage_use_case"), table_name="ai_usage")
    op.drop_index(op.f("ix_ai_usage_model"), table_name="ai_usage")
    op.drop_index(op.f("ix_ai_usage_provider"), table_name="ai_usage")
    op.drop_index(op.f("ix_ai_usage_id"), table_name="ai_usage")
    op.drop_table("ai_usage")
