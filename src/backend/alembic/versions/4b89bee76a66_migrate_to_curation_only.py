"""migrate_to_curation_only

Revision ID: 4b89bee76a66
Revises: 6cf222bd9228
Create Date: 2026-01-12 19:16:13.566384

MIGRATION POLICY: REMOVES LEGACY TABLES
- Drops legacy articles, videos, tags, article_tag tables
- Updates conversations to reference content_items
- Updates usage to reference content_items
- This is a breaking migration - data will be lost
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "4b89bee76a66"
down_revision = "6cf222bd9228"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Drop conversations foreign key constraint if it exists
    if "conversations" in inspector.get_table_names():
        # Check if article_id column exists (means migration not run yet)
        columns = {c["name"] for c in inspector.get_columns("conversations")}
        if "article_id" in columns:
            # Check if constraint exists before dropping
            fks = inspector.get_foreign_keys("conversations")
            if any(fk["name"] == "conversations_article_id_fkey" for fk in fks):
                op.drop_constraint(
                    "conversations_article_id_fkey", "conversations", type_="foreignkey"
                )
            # Rename article_id to content_item_id
            op.alter_column("conversations", "article_id", new_column_name="content_item_id")
            # Add new foreign key to content_items
            op.create_foreign_key(
                "conversations_content_item_id_fkey",
                "conversations",
                "content_items",
                ["content_item_id"],
                ["id"],
            )

    # Drop usage foreign key constraint if it exists
    if "usage" in inspector.get_table_names():
        # Check if article_id column exists
        columns = {c["name"] for c in inspector.get_columns("usage")}
        if "article_id" in columns:
            # Check if constraint exists before dropping
            fks = inspector.get_foreign_keys("usage")
            if any(fk["name"] == "usage_article_id_fkey" for fk in fks):
                op.drop_constraint("usage_article_id_fkey", "usage", type_="foreignkey")
            # Rename article_id to content_item_id
            op.alter_column("usage", "article_id", new_column_name="content_item_id")
            # Add new foreign key to content_items
            op.create_foreign_key(
                "usage_content_item_id_fkey", "usage", "content_items", ["content_item_id"], ["id"]
            )

    # Drop legacy tables (respecting foreign key order)
    if "article_tag" in inspector.get_table_names():
        op.drop_table("article_tag")

    if "articles" in inspector.get_table_names():
        op.drop_table("articles")

    if "videos" in inspector.get_table_names():
        op.drop_table("videos")

    if "tags" in inspector.get_table_names():
        op.drop_table("tags")


def downgrade() -> None:
    # This is a destructive migration - downgrade not supported
    raise NotImplementedError("Cannot downgrade from curation-only schema")
