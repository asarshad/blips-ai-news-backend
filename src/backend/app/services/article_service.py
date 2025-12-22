
"""Article service for business logic related to articles."""

from typing import List, Dict, Any, Optional
import json
import redis

from app.core.config import settings
from app.core.logging import get_logger
from app.core.exceptions import ArticleNotFoundError
from app.models.article import Article
from app.repositories.article_repo import ArticleRepository

logger = get_logger(__name__)

CACHE_TTL_SECONDS = 60 * 15  # 15 minutes
CACHE_DAYS = 3  # Cache articles from the last 3 days


class ArticleService:
    """Service for article-related business logic."""
    
    def __init__(self, article_repo: ArticleRepository, redis_client: redis.Redis):
        self.repo = article_repo
        self.redis = redis_client
    
    def get_next_article(self, current_id: Optional[int] = None) -> Optional[Article]:
        """
        Get next article after current_id, or the most recent if current_id is None.
        
        Args:
            current_id: ID of current article, or None to get most recent
            
        Returns:
            Next Article or None if no articles exist
        """
        if current_id:
            next_article = self.repo.get_next_after(current_id)
            if next_article:
                return next_article
        
        return self.repo.get_most_recent()
    
    def get_article_by_id(self, article_id: int) -> Article:
        """
        Get article by ID.
        
        Args:
            article_id: ID of the article
            
        Returns:
            Article instance
            
        Raises:
            ArticleNotFoundError: If article doesn't exist
        """
        article = self.repo.get_by_id(article_id)
        if not article:
            raise ArticleNotFoundError(article_id)
        return article
    
    def get_articles_by_tag(self, tag_name: str, limit: int = 10) -> List[Article]:
        """Get articles by tag name."""
        return self.repo.get_by_tag(tag_name, limit)
    
    def get_recent_articles(self, limit: int = 5, page: int = 1) -> List[Article]:
        """Get most recent articles."""
        skip = (page - 1) * limit
        return self.repo.get_recent(limit, skip)
    
    def cache_articles(self) -> bool:
        """
        Cache articles from the last 3 days in Redis.
        Ordered by date descending, then by hot_score within each date.
        
        Returns:
            True if caching succeeded, False otherwise
        """
        try:
            articles = self.repo.get_articles_last_n_days(CACHE_DAYS)
            
            cached_articles = [
                {
                    "id": article.id,
                    "title": article.title,
                    "summary": article.summary,
                    "source_url": article.source_url,
                    "image_url": article.image_url,
                    "published_date": article.published_date.isoformat() if article.published_date else None,
                    "created_at": article.created_at.isoformat(),
                    "hot_score": article.hot_score or 0,
                    "read_time_minutes": article.read_time_minutes or 1,
                    "tags": [{"name": tag.name} for tag in article.tags]  # Format as Tag objects
                }
                for article in articles
            ]
            
            self.redis.setex(
                "cached_articles",
                CACHE_TTL_SECONDS,
                json.dumps(cached_articles)
            )
            
            logger.info(f"Cached {len(cached_articles)} articles in Redis")
            return True
        
        except Exception as e:
            logger.error(f"Error caching articles: {str(e)}")
            return False
    
    def get_cached_articles(self) -> List[Dict[str, Any]]:
        """
        Get cached articles from Redis.
        
        Returns:
            List of article dicts, or empty list if cache miss
        """
        try:
            cached_data = self.redis.get("cached_articles")
            
            if cached_data:
                return json.loads(cached_data)
            
            # Cache miss - populate and return
            self.cache_articles()
            cached_data = self.redis.get("cached_articles")
            
            return json.loads(cached_data) if cached_data else []
            
        except Exception as e:
            logger.error(f"Error getting cached articles: {str(e)}")
            return []
    
    def get_popular_tags(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most popular tags with article counts."""
        return self.repo.get_popular_tags(limit)
