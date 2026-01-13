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

from app.db.base import SessionLocal
from app.core.logging import get_logger
from app.core.feature_flags import feature_flags

logger = get_logger(__name__)


def trigger_ingestion():
    """Manually trigger content ingestion."""
    print("=" * 60)
    print("MANUAL INGESTION TRIGGER")
    print("=" * 60)
    
    # Check feature flags
    print("\n[1/5] Checking feature flags...")
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
    print("\n[2/5] Connecting to database...")
    db = SessionLocal()
    
    try:
        # Import services
        from app.repositories.article_repo import ArticleRepository
        from app.repositories.video_repo import VideoRepository
        from app.services.news_fetcher import NewsFetcher
        from app.services.video_fetcher import VideoFetcher
        from app.services.summarizer import ArticleSummarizer
        
        article_repo = ArticleRepository(db)
        video_repo = VideoRepository(db)
        
        # Fetch articles
        print("\n[3/5] Fetching articles from RSS feeds...")
        news_fetcher = NewsFetcher(article_repo)
        articles = news_fetcher.fetch_latest_articles()
        print(f"  Found {len(articles)} new articles")
        
        # Process articles
        if articles:
            print("\n[4/5] Processing articles...")
            summarizer = ArticleSummarizer(article_repo)
            processed = 0
            failed = 0
            
            for i, article_data in enumerate(articles[:50]):  # Limit to 50 for manual run
                try:
                    # Skip summarization if disabled
                    processed_article = summarizer.summarize_article(
                        article_data,
                        skip_summarization=not summarization_enabled
                    )
                    summarizer.save_article(processed_article)
                    db.commit()
                    processed += 1
                    
                    if (i + 1) % 10 == 0:
                        print(f"  Processed {i + 1}/{min(len(articles), 50)} articles...")
                        
                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to process article: {e}")
                    db.rollback()
            
            print(f"  ✓ Processed: {processed}, Failed: {failed}")
        else:
            print("\n[4/5] No new articles to process")
        
        # Fetch videos
        if videos_enabled:
            print("\n[5/5] Fetching videos...")
            video_fetcher = VideoFetcher(video_repo)
            videos = video_fetcher.fetch_latest_videos()
            
            if videos:
                saved_count = video_fetcher.save_videos(videos)
                print(f"  ✓ Saved {saved_count} new videos")
            else:
                print("  No new videos found")
        else:
            print("\n[5/5] Video fetch skipped (feature disabled)")
        
        # Run curation ingestion
        print("\n[BONUS] Running curation ingestion...")
        try:
            from app.services.ingestion_pipeline import create_ingestion_pipeline
            pipeline = create_ingestion_pipeline(db)
            result = pipeline.run_backfill(hours_back=24, limit=500)
            print(f"  ✓ Curation result: {result}")
        except Exception as e:
            print(f"  ⚠ Curation failed: {e}")
        
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
        result = db.execute(text("""
            SELECT type, COUNT(*) as count
            FROM content_items
            GROUP BY type
            ORDER BY type
        """))
        
        print("\nContent items by type:")
        total = 0
        for row in result:
            print(f"  - {row[0]}: {row[1]}")
            total += row[1]
        print(f"  TOTAL: {total}")
        
        # Check for duplicate URLs
        result = db.execute(text("""
            SELECT source_url, COUNT(*) as count
            FROM content_items
            GROUP BY source_url
            HAVING COUNT(*) > 1
            LIMIT 10
        """))
        
        duplicates = list(result)
        if duplicates:
            print(f"\n⚠ Found {len(duplicates)} duplicate URLs:")
            for row in duplicates[:5]:
                print(f"  - {row[0][:60]}... ({row[1]} copies)")
        else:
            print("\n✓ No duplicate URLs found")
        
        # Check recent content
        result = db.execute(text("""
            SELECT type, title, created_at
            FROM content_items
            ORDER BY created_at DESC
            LIMIT 5
        """))
        
        print("\nMost recent content:")
        for row in result:
            print(f"  - [{row[0]}] {row[1][:50]}...")
        
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Manual ingestion trigger")
    parser.add_argument("--verify-only", action="store_true", help="Only verify content, don't ingest")
    args = parser.parse_args()
    
    if args.verify_only:
        check_content_items()
    else:
        trigger_ingestion()
        check_content_items()
