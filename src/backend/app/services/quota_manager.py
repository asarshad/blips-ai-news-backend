
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from app.models.usage import Usage
from app.core.config import settings
import redis
import json
import logging

logger = logging.getLogger(__name__)

class QuotaManager:
    def __init__(self, db: Session, redis_client: redis.Redis):
        self.db = db
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
            today = datetime.utcnow().date()
            tomorrow = today + timedelta(days=1)
            
            # Daily usage
            daily_usage = self.db.query(func.sum(Usage.message_count)).filter(
                Usage.device_id == device_id,
                Usage.timestamp >= today,
                Usage.timestamp < tomorrow
            ).scalar() or 0
            
            remaining_daily = max(0, self.max_per_day - daily_usage)
            
            # Article specific usage (if applicable)
            remaining_article = None
            if article_id:
                article_usage = self.db.query(func.sum(Usage.message_count)).filter(
                    Usage.device_id == device_id,
                    Usage.article_id == article_id
                ).scalar() or 0
                
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
            usage = Usage(
                device_id=device_id,
                article_id=article_id,
                used_tokens=tokens,
                message_count=1,
                timestamp=datetime.utcnow()
            )
            
            self.db.add(usage)
            self.db.commit()
            
            # Invalidate cache
            cache_key = f"quota:{device_id}"
            self.redis.delete(cache_key)
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating usage: {str(e)}")
