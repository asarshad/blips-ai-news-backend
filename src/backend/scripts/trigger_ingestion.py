#!/usr/bin/env python3
"""
Manual ingestion trigger script.

Run this after deployment to populate initial content.
Can be run locally with DB connection or via Render shell.

Usage:
    python scripts/trigger_ingestion.py

Environment:
    DATABASE_URL - PostgreSQL connection string
    REDIS_URL - Redis connection string (optional)
"""

import os
import sys

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal

logger = get_logger(__name__)


def trigger_ingestion():
    """Manually trigger content ingestion."""
    print("=" * 60)
    print("MANUAL INGESTION TRIGGER")
    print("=" * 60)

    # Check feature flags
    print("\n[1/3] Checking feature flags...")
    ingestion_enabled = feature_flags.is_enabled("ingestion")
    summarization_enabled = feature_flags.is_enabled("summarization")
    videos_enabled = feature_flags.is_enabled("videos")

    print(f"  - Ingestion: {'✓ ENABLED' if ingestion_enabled else '✗ DISABLED'}")
    print(f"  - Summarization: {'✓ ENABLED' if summarization_enabled else '✗ DISABLED'}")
    print(f"  - Videos: {'✓ ENABLED' if videos_enabled else '✗ DISABLED'}")

    if not ingestion_enabled:
        print("\n❌ ABORTED: Ingestion feature is disabled")
        print("Enable with: PUT /api/v1/admin/flags/ingestion {'enabled': true}")
        return

    # Connect to database
    print("\n[2/3] Connecting to database...")
    db = SessionLocal()

    try:
        # Run ingestion pipeline
        print("\n[3/3] Running ingestion pipeline...")
        from app.ingestion.service import create_ingestion_pipeline

        pipeline = create_ingestion_pipeline(db)
        result = pipeline.run_backfill(hours_back=24, limit=500)

        print("\nIngestion results:")
        print(f"  - Articles processed: {result['articles_processed']}")
        print(f"  - Articles ingested: {result['articles_ingested']}")
        print(f"  - Videos processed: {result['videos_processed']}")
        print(f"  - Videos ingested: {result['videos_ingested']}")
        print(f"  - Errors: {result['errors']}")

        print("\n" + "=" * 60)
        print("INGESTION COMPLETE")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def check_content_items():
    """Check content_items table status."""
    print("\n" + "=" * 60)
    print("CONTENT VERIFICATION")
    print("=" * 60)

    db = SessionLocal()
    try:
        from sqlalchemy import text

        # Count content items
        result = db.execute(
            text("""
            SELECT type, COUNT(*) as count
            FROM content_items
            GROUP BY type
            ORDER BY type
        """)
        )

        print("\nContent items by type:")
        total = 0
        for row in result:
            print(f"  - {row[0]}: {row[1]}")
            total += row[1]
        print(f"  TOTAL: {total}")

        # Check for duplicate URLs
        result = db.execute(
            text("""
            SELECT source_url, COUNT(*) as count
            FROM content_items
            GROUP BY source_url
            HAVING COUNT(*) > 1
            LIMIT 10
        """)
        )

        duplicates = list(result)
        if duplicates:
            print(f"\n⚠ Found {len(duplicates)} duplicate URLs:")
            for row in duplicates[:5]:
                print(f"  - {row[0][:60]}... ({row[1]} copies)")
        else:
            print("\n✓ No duplicate URLs found")

        # Check recent content
        result = db.execute(
            text("""
            SELECT type, title, created_at
            FROM content_items
            ORDER BY created_at DESC
            LIMIT 5
        """)
        )

        print("\nMost recent content:")
        for row in result:
            print(f"  - [{row[0]}] {row[1][:50]}...")

    finally:
        db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Manual ingestion trigger")
    parser.add_argument(
        "--verify-only", action="store_true", help="Only verify content, don't ingest"
    )
    args = parser.parse_args()

    if args.verify_only:
        check_content_items()
    else:
        trigger_ingestion()
        check_content_items()
