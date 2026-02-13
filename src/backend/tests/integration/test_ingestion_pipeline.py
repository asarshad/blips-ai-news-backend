"""
Integration tests for the ingestion pipeline.

Tests critical ingestion functionality:
- Language filter integration
- Content deduplication
- Feed parsing
"""

import pytest
from unittest.mock import MagicMock, patch

pytestmark = [pytest.mark.integration]


class TestLanguageFilterIntegration:
    """Test language filter in ingestion context."""
    
    def test_spanish_content_rejected_in_rss_ingestion(self):
        """Spanish articles should be rejected during RSS ingestion."""
        from app.ingestion.language_filter import is_english
        
        # Spanish article title
        title = "El iPhone 16 llega con nuevas características revolucionarias"
        description = "Apple ha lanzado su nuevo smartphone con mejoras significativas en el procesador y la cámara."
        
        result = is_english(title, description)
        assert result is False, "Spanish content should be rejected"
    
    def test_english_content_accepted(self):
        """English articles should pass the language filter."""
        from app.ingestion.language_filter import is_english
        
        title = "Apple launches iPhone 16 with revolutionary new features"
        description = "Apple has released its new smartphone with significant improvements to the processor and camera."
        
        result = is_english(title, description)
        assert result is True, "English content should be accepted"
    
    def test_mixed_language_mostly_english_accepted(self):
        """Content that is mostly English should be accepted."""
        from app.ingestion.language_filter import is_english
        
        # Mostly English with some non-English words
        title = "Google announces new AI features for Android"
        description = "The tech giant unveiled several innovations at the event. These include new machine learning capabilities for developers."
        
        result = is_english(title, description)
        assert result is True
    
    def test_very_short_content_accepted(self):
        """Very short content should be accepted (not enough for detection)."""
        from app.ingestion.language_filter import is_english
        
        title = "News"
        description = ""
        
        result = is_english(title, description)
        assert result is True, "Short content should pass (safe default)"


class TestContentDeduplication:
    """Test content deduplication helpers."""
    
    def test_dedupe_key_generation(self):
        """Dedupe keys should be generated consistently."""
        from app.ingestion.deduplication import generate_dedupe_key
        
        # Same source should generate same key
        key1 = generate_dedupe_key("https://example.com/article/123")
        key2 = generate_dedupe_key("https://example.com/article/123")
        assert key1 == key2
        
        # Different sources should generate different keys
        key3 = generate_dedupe_key("https://example.com/article/456")
        assert key1 != key3
    
    def test_url_normalization(self):
        """URLs should be normalized for deduplication."""
        from app.ingestion.deduplication import normalize_url
        
        # With and without trailing slash
        url1 = normalize_url("https://example.com/article/")
        url2 = normalize_url("https://example.com/article")
        assert url1 == url2
        
        # With and without www
        url3 = normalize_url("https://www.example.com/article")
        url4 = normalize_url("https://example.com/article")
        assert url3 == url4


class TestYouTubeClientSafety:
    """Test YouTube client ToS compliance."""
    
    def test_transcript_method_returns_none(self):
        """get_transcript should return None (deprecated for ToS compliance)."""
        from app.integrations.youtube_client import YouTubeClient
        
        client = YouTubeClient()
        result = client.get_transcript("some-video-id")
        
        # Should always return None now that youtube-transcript-api is removed
        assert result is None, "get_transcript should return None (deprecated)"
    
    def test_duration_uses_api_not_scraping(self):
        """Video duration should use YouTube Data API when available."""
        from app.integrations.youtube_client import YouTubeClient
        import os
        
        # With API key set, should try API first
        with patch.dict(os.environ, {"YOUTUBE_API_KEY": "test-key"}):
            client = YouTubeClient()
            
            # Mock the API call to verify it's attempted
            with patch.object(client, '_get_duration_from_api', return_value=300) as mock_api:
                duration = client.get_video_duration("test-video-id")
                mock_api.assert_called_once_with("test-video-id", "test-key")
                assert duration == 300
