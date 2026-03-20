"""add_content_quality_fields

Revision ID: b7d1f4a2c9e0
Revises: 3c2d5e7a8b9c
Create Date: 2026-02-04

MIGRATION POLICY: ADDITIVE ONLY
- Adds canonical_key for hard dedupe, ingestion_day for strict targets
- Adds suppression + text + simhash fields for quality and near-duplicate handling
- Backfills canonical_key best-effort and suppresses pre-existing canonical duplicates
"""

from __future__ import annotations

import hashlib
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b7d1f4a2c9e0"
down_revision = "3c2d5e7a8b9c"
branch_labels = None
depends_on = None


_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_name",
    "utm_reader",
    "utm_referrer",
    "utm_social",
    "utm_social-type",
    "utm_brand",
    "utm_cid",
    "utm_sid",
    "gclid",
    "fbclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}


def _normalize_url_for_key(url: str) -> str:
    if not url:
        return url

    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    netloc = (parts.netloc or "").lower()

    query_pairs = [
        (k, v)
        for (k, v) in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(query_pairs, doseq=True)

    path = parts.path or ""
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    # Always drop fragment.
    fragment = ""

    return urlunsplit((scheme, netloc, path, query, fragment))


def _extract_youtube_id(url: str) -> Optional[str]:
    if not url:
        return None
    lowered = url.lower()
    # Common URL forms.
    if "youtu.be/" in lowered:
        try:
            after = url.split("youtu.be/", 1)[1]
            return after.split("?", 1)[0].split("#", 1)[0].split("&", 1)[0]
        except Exception:
            return None

    if "youtube.com" in lowered:
        if "/shorts/" in lowered:
            try:
                after = url.split("/shorts/", 1)[1]
                return after.split("?", 1)[0].split("#", 1)[0].split("&", 1)[0]
            except Exception:
                return None
        if "watch" in lowered and "v=" in lowered:
            try:
                query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
                vid = query.get("v")
                return vid
            except Exception:
                return None

    return None


def _canonical_key(
    content_type: str, canonical_url: Optional[str], source_url: str, video_url: Optional[str]
) -> Optional[str]:
    # VIDEO/REEL: prefer YouTube video_id if present.
    if content_type in ("VIDEO", "REEL"):
        vid = _extract_youtube_id(video_url or "") or _extract_youtube_id(source_url or "")
        if vid:
            return vid

    # ARTICLE (and fallback): hash normalized canonical URL (or source URL).
    base = canonical_url or source_url
    if not base:
        return None

    normalized = _normalize_url_for_key(base)
    if not normalized:
        return None

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def upgrade() -> None:
    conn = op.get_bind()

    # Add columns
    op.add_column("content_items", sa.Column("canonical_key", sa.String(length=64), nullable=True))
    op.add_column("content_items", sa.Column("ingestion_day", sa.Date(), nullable=True))
    op.add_column(
        "content_items",
        sa.Column("is_suppressed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("content_items", sa.Column("content_text", sa.Text(), nullable=True))
    op.add_column("content_items", sa.Column("simhash", sa.BigInteger(), nullable=True))

    op.create_index("ix_content_items_canonical_key", "content_items", ["canonical_key"])
    op.create_index("ix_content_items_ingestion_day", "content_items", ["ingestion_day"])
    op.create_index("ix_content_items_is_suppressed", "content_items", ["is_suppressed"])
    op.create_index("ix_content_items_simhash", "content_items", ["simhash"])
    op.create_index(
        "ix_content_items_ingestion_day_type", "content_items", ["ingestion_day", "type"]
    )

    # Backfill ingestion_day best-effort from created_at (UTC date).
    conn.execute(
        sa.text(
            "UPDATE content_items SET ingestion_day = DATE(created_at) WHERE ingestion_day IS NULL"
        )
    )

    # Backfill canonical_key in batches.
    batch_size = 500
    last_id = 0

    while True:
        rows = conn.execute(
            sa.text(
                """
                SELECT id, type::text AS type, canonical_url, source_url, video_url
                FROM content_items
                WHERE id > :last_id AND canonical_key IS NULL
                ORDER BY id ASC
                LIMIT :limit
                """
            ),
            {"last_id": last_id, "limit": batch_size},
        ).fetchall()

        if not rows:
            break

        for r in rows:
            cid = int(r.id)
            ctype = str(r.type)
            ck = _canonical_key(ctype, r.canonical_url, r.source_url, r.video_url)
            if ck:
                conn.execute(
                    sa.text("UPDATE content_items SET canonical_key = :ck WHERE id = :id"),
                    {"ck": ck, "id": cid},
                )
            last_id = cid

    # Suppress pre-existing canonical duplicates deterministically.
    # Keep the lowest id as canonical; suppress + null out canonical_key on others.
    conn.execute(
        sa.text(
            """
            WITH ranked AS (
              SELECT id,
                     ROW_NUMBER() OVER (PARTITION BY type, canonical_key ORDER BY id ASC) AS rn
              FROM content_items
              WHERE canonical_key IS NOT NULL
            )
            UPDATE content_items
            SET is_suppressed = true,
                canonical_key = NULL
            WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
            """
        )
    )

    # Enforce hard-dedupe for future inserts.
    # Postgres allows multiple NULLs, so we keep canonical_key nullable.
    op.create_index(
        "uq_content_items_type_canonical_key",
        "content_items",
        ["type", "canonical_key"],
        unique=True,
        postgresql_where=sa.text("canonical_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_content_items_type_canonical_key", table_name="content_items")

    op.drop_index("ix_content_items_ingestion_day_type", table_name="content_items")
    op.drop_index("ix_content_items_simhash", table_name="content_items")
    op.drop_index("ix_content_items_is_suppressed", table_name="content_items")
    op.drop_index("ix_content_items_ingestion_day", table_name="content_items")
    op.drop_index("ix_content_items_canonical_key", table_name="content_items")

    op.drop_column("content_items", "simhash")
    op.drop_column("content_items", "content_text")
    op.drop_column("content_items", "is_suppressed")
    op.drop_column("content_items", "ingestion_day")
    op.drop_column("content_items", "canonical_key")
