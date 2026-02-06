"""
Unit tests for tiered feed service.

Tests the tier selection logic, caching, and diversity mixing.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services.inventory_service import FreshnessTier, Surface


class TestTierSelection:
    """Test tier assignment logic."""
    
    def test_tier_a_fresh_published(self):
        """Items published within fresh window get Tier A."""
        from app.services.tiered_feed_service import _annotate_item
        
        now = datetime(2026, 2, 6, 12, 0, 0)
        item = MagicMock()
        item.published_at = now - timedelta(hours=2)  # 2 hours ago
        item.created_at = now - timedelta(hours=1)
        
        result = _annotate_item(item, FreshnessTier.A, "fresh_published", now)
        
        assert result.tier == FreshnessTier.A
        assert result.reason == "fresh_published"
        assert 7100 < result.published_age_seconds < 7300  # ~2 hours
        assert 3500 < result.added_age_seconds < 3700  # ~1 hour
    
    def test_tier_b_recently_added(self):
        """Items added recently but published earlier get Tier B."""
        from app.services.tiered_feed_service import _annotate_item
        
        now = datetime(2026, 2, 6, 12, 0, 0)
        item = MagicMock()
        item.published_at = now - timedelta(days=2)  # Published 2 days ago
        item.created_at = now - timedelta(hours=6)  # Added 6 hours ago
        
        result = _annotate_item(item, FreshnessTier.B, "recently_added", now)
        
        assert result.tier == FreshnessTier.B
        assert result.reason == "recently_added"
        assert result.published_age_seconds > 172000  # > 2 days
        assert result.added_age_seconds < 22000  # < 6 hours
    
    def test_tier_c_evergreen(self):
        """Older quality items get Tier C."""
        from app.services.tiered_feed_service import _annotate_item
        
        now = datetime(2026, 2, 6, 12, 0, 0)
        item = MagicMock()
        item.published_at = now - timedelta(days=5)
        item.created_at = now - timedelta(days=4)
        
        result = _annotate_item(item, FreshnessTier.C, "evergreen", now)
        
        assert result.tier == FreshnessTier.C
        assert result.reason == "evergreen"


class TestTieredItemToDict:
    """Test serialization of tiered items."""
    
    def test_article_serialization(self):
        """Articles include read_time and tags."""
        from app.services.tiered_feed_service import tiered_item_to_dict, TieredItem
        from app.models.content import ContentType
        
        now = datetime(2026, 2, 6, 12, 0, 0)
        item = MagicMock()
        item.id = 123
        item.title = "Test Article"
        item.source_url = "https://example.com/article"
        item.summary = "A" * 400  # 400 chars = 2 min read
        item.image_url = "https://example.com/img.jpg"
        item.source = "Example News"
        item.created_at = now - timedelta(hours=1)
        item.published_at = now - timedelta(hours=2)
        item.type = ContentType.ARTICLE
        item.topics = ["AI", "Technology"]
        
        tiered = TieredItem(
            item=item,
            tier=FreshnessTier.A,
            reason="fresh_published",
            published_age_seconds=7200,
            added_age_seconds=3600,
        )
        
        result = tiered_item_to_dict(tiered)
        
        assert result["id"] == 123
        assert result["title"] == "Test Article"
        assert result["freshness_tier"] == "A"
        assert result["freshness_reason"] == "fresh_published"
        assert result["published_age_seconds"] == 7200
        assert result["added_age_seconds"] == 3600
        assert result["read_time_minutes"] == 2
        assert result["tags"] == [{"name": "AI"}, {"name": "Technology"}]
    
    def test_video_serialization(self):
        """Videos include video_url, thumbnail, category."""
        from app.services.tiered_feed_service import tiered_item_to_dict, TieredItem
        from app.models.content import ContentType
        
        now = datetime(2026, 2, 6, 12, 0, 0)
        item = MagicMock()
        item.id = 456
        item.title = "Test Video"
        item.source_url = "https://youtube.com/watch?v=abc"
        item.video_url = "https://youtube.com/watch?v=abc"
        item.summary = "Video summary"
        item.image_url = "https://img.youtube.com/vi/abc/hq.jpg"
        item.source = "TechChannel"
        item.created_at = now - timedelta(hours=1)
        item.published_at = now - timedelta(hours=3)
        item.type = ContentType.VIDEO
        item.topics = ["Tutorials"]
        item.duration_seconds = 600
        item.global_score = 0.85
        
        tiered = TieredItem(
            item=item,
            tier=FreshnessTier.B,
            reason="recently_added",
            published_age_seconds=10800,
            added_age_seconds=3600,
        )
        
        result = tiered_item_to_dict(tiered)
        
        assert result["id"] == 456
        assert result["freshness_tier"] == "B"
        assert result["video_url"] == "https://youtube.com/watch?v=abc"
        assert result["thumbnail_url"] == "https://img.youtube.com/vi/abc/hq.jpg"
        assert result["category"] == "Tutorials"
        assert result["duration_seconds"] == 600
        assert result["hot_score"] == 85


class TestSurfaceConfig:
    """Test surface-specific configuration."""
    
    def test_articles_config(self):
        """Articles have appropriate windows."""
        from app.services.inventory_service import _get_surface_config
        
        cfg = _get_surface_config(Surface.ARTICLES)
        
        assert cfg["fresh_hours"] == 36
        assert cfg["backfill_hours"] == 24
        assert cfg["evergreen_days"] == 14
        assert cfg["min_fresh"] == 30
        assert cfg["reservoir"] == 200
    
    def test_videos_config(self):
        """Videos have longer windows than articles."""
        from app.services.inventory_service import _get_surface_config
        
        cfg = _get_surface_config(Surface.VIDEOS)
        
        assert cfg["fresh_hours"] > 36  # Longer than articles
        assert cfg["evergreen_days"] > 14
    
    def test_reels_config(self):
        """Reels have the longest windows."""
        from app.services.inventory_service import _get_surface_config
        
        cfg = _get_surface_config(Surface.REELS)
        
        assert cfg["fresh_hours"] == 168  # 7 days
        assert cfg["evergreen_days"] == 45


class TestCacheInvalidation:
    """Test cache invalidation logic."""
    
    @patch("app.services.tiered_feed_service._get_redis_client")
    def test_invalidate_all_surfaces(self, mock_redis):
        """Invalidating without surface clears all caches."""
        from app.services.tiered_feed_service import invalidate_tiered_feed_cache
        
        mock_client = MagicMock()
        mock_client.keys.return_value = [b"key1", b"key2", b"key3"]
        mock_redis.return_value = mock_client
        
        invalidate_tiered_feed_cache()
        
        mock_client.keys.assert_called_once_with("blips:tiered_feed:*")
        mock_client.delete.assert_called_once()
    
    @patch("app.services.tiered_feed_service._get_redis_client")
    def test_invalidate_specific_surface(self, mock_redis):
        """Invalidating with surface only clears that surface."""
        from app.services.tiered_feed_service import invalidate_tiered_feed_cache
        
        mock_client = MagicMock()
        mock_client.keys.return_value = [b"key1"]
        mock_redis.return_value = mock_client
        
        invalidate_tiered_feed_cache(Surface.ARTICLES)
        
        mock_client.keys.assert_called_once_with("blips:tiered_feed:articles:*")
    
    @patch("app.services.tiered_feed_service._get_redis_client")
    def test_invalidate_handles_no_redis(self, mock_redis):
        """Gracefully handles Redis unavailability."""
        from app.services.tiered_feed_service import invalidate_tiered_feed_cache
        
        mock_redis.return_value = None
        
        # Should not raise
        invalidate_tiered_feed_cache()
