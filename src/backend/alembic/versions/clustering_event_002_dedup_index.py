"""Partial unique index to dedup pending clustering events.

A single-leader ingestion lane normally guarantees only one
queue_content_clustering_request runs at a time, but the SELECT-then-INSERT
dedup is racy if anything ever runs in parallel (manual trigger, retried
fetch_news, etc). A partial unique index makes the database the source of
truth so duplicates fail loudly instead of slipping through.

Revision ID: clustering_event_002
Revises: clustering_event_001
Create Date: 2026-05-03
"""

from __future__ import annotations

from alembic import op

revision = "clustering_event_002"
down_revision = "clustering_event_001"
branch_labels = None
depends_on = None


# Postgres-only: partial unique index covering exactly the pending/processing
# pool the dedup query checks. event_type='content.clustering.requested' is the
# only "global" (content_item_id IS NULL) event, but we scope the index to that
# event type explicitly so future global event types don't collide on it.
_INDEX_NAME = "uq_content_event_outbox_clustering_pending"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME}
        ON content_event_outbox ((event_type))
        WHERE event_type = 'content.clustering.requested'
          AND status IN ('pending', 'processing')
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
