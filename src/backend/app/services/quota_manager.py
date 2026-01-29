"""Quota management service for tracking user API usage."""

import json
from datetime import timedelta
from typing import Optional

import redis

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.usage_repo import UsageRepository

logger = get_logger(__name__)

QUOTA_CACHE_TTL = timedelta(minutes=5)


class QuotaManager:
    """Service for managing user quota and usage tracking."""
    
    def __init__(self, usage_repo: UsageRepository, redis_client: redis.Redis):
        self.usage_repo = usage_repo
        self.redis = redis_client
        self.max_per_day = settings.MAX_MESSAGES_PER_DAY
        self.max_per_article = settings.MAX_MESSAGES_PER_ARTICLE
    
    def check_quota(self, device_id: str, article_id: Optional[int] = None) -> dict:
        """
        Check if the user has available quota.
        
        Args:
            device_id: Unique device identifier
            article_id: Optional article ID for article-specific quota
            
        Returns:
            Dict with 'remaining_daily_messages' and 'remaining_article_messages'
        """
        cache_key = f"quota:{device_id}"
        
        # Try cache first
        cached_data = self._get_cached_quota(cache_key)
        if cached_data:
            return cached_data
        
        # Calculate from database
        daily_usage = self.usage_repo.get_daily_usage(device_id)
        remaining_daily = max(0, self.max_per_day - daily_usage)
        
        remaining_article = None
        if article_id:
            article_usage = self.usage_repo.get_article_usage(device_id, article_id)
            remaining_article = max(0, self.max_per_article - article_usage)
        
        quota_data = {
            "remaining_daily_messages": remaining_daily,
            "remaining_article_messages": remaining_article
        }
        
        self._cache_quota(cache_key, quota_data)
        return quota_data
    
    def update_usage(self, device_id: str, article_id: Optional[int] = None, tokens: int = 0) -> None:
        """
        Update usage after a successful interaction.
        
        Args:
            device_id: Unique device identifier
            article_id: Optional article ID
            tokens: Number of tokens used
        """
        self.usage_repo.record_usage(device_id, article_id, tokens)
        
        # Invalidate cache
        cache_key = f"quota:{device_id}"
        self.redis.delete(cache_key)
    
    def _get_cached_quota(self, cache_key: str) -> Optional[dict]:
        """Get quota data from cache."""
        try:
            cached_data = self.redis.get(cache_key)
            if cached_data:
                return json.loads(cached_data)
        except Exception as e:
            logger.warning(f"Cache read error: {str(e)}")
        return None
    
    def _cache_quota(self, cache_key: str, quota_data: dict) -> None:
        """Cache quota data."""
        try:
            # redis-py `setex` expects an integer number of seconds.
            # Passing a timedelta is not consistently supported (e.g., fakeredis),
            # which can silently disable caching in tests.
            ttl_seconds = int(QUOTA_CACHE_TTL.total_seconds())
            self.redis.setex(cache_key, ttl_seconds, json.dumps(quota_data))
        except Exception as e:
            logger.warning(f"Cache write error: {str(e)}")
