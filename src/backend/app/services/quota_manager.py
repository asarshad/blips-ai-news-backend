
from datetime import datetime, timedelta
from app.models.usage import Usage
from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.usage_repo import UsageRepository
import redis
import json

logger = get_logger(__name__)

class QuotaManager:
    def __init__(self, usage_repo: UsageRepository, redis_client: redis.Redis):
        self.usage_repo = usage_repo
        self.redis = redis_client
        self.max_per_day = settings.MAX_MESSAGES_PER_DAY
        self.max_per_article = settings.MAX_MESSAGES_PER_ARTICLE
    
    def check_quota(self, device_id: str, article_id: int = None) -> dict:
        """Check if the user has available quota"""
        try:
            # Check Redis cache first for faster lookup
            cache_key = f"quota:{device_id}"
            cached_data = self.redis.get(cache_key)
            
            if cached_data:
                quota_data = json.loads(cached_data)
                return quota_data
            
            # Calculate quota from database
            daily_usage = self.usage_repo.get_daily_usage(device_id)
            remaining_daily = max(0, self.max_per_day - daily_usage)
            
            # Article specific usage (if applicable)
            remaining_article = None
            if article_id:
                article_usage = self.usage_repo.get_article_usage(device_id, article_id)
                remaining_article = max(0, self.max_per_article - article_usage)
            
            # Cache the result
            quota_data = {
                "remaining_daily_messages": remaining_daily,
                "remaining_article_messages": remaining_article
            }
            
            self.redis.setex(
                cache_key,
                timedelta(minutes=5),  # Cache for 5 minutes
                json.dumps(quota_data)
            )
            
            return quota_data
            
        except Exception as e:
            logger.error(f"Error checking quota: {str(e)}")
            # Default to allowing usage if there's an error
            return {
                "remaining_daily_messages": 1, 
                "remaining_article_messages": 1 if article_id else None
            }
    
    def update_usage(self, device_id: str, article_id: int = None, tokens: int = 0):
        """Update usage after a successful interaction"""
        try:
            # Create new usage record
            self.usage_repo.record_usage(device_id, article_id, tokens)
            
            # Invalidate cache
            cache_key = f"quota:{device_id}"
            self.redis.delete(cache_key)
            
        except Exception as e:
            logger.error(f"Error updating usage: {str(e)}")
