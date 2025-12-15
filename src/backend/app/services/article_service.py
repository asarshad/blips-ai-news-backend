
"""
Article service for business logic related to articles.
"""

from typing import List, Dict, Any, Optional
import json
import redis

from app.core.config import settings
from app.core.logging import get_logger
from app.models.article import Article
from app.repositories.article_repo import ArticleRepository

logger = get_logger(__name__)


class ArticleService:
    """Service for article-related business logic."""
    
    def __init__(self, article_repo: ArticleRepository, redis_client: redis.Redis):
        self.repo = article_repo
        self.redis = redis_client
        self.cache_count = settings.ARTICLE_CACHE_COUNT
    
    def get_next_article(self, current_id: int = None) -> Optional[Article]:
        """Get next article after current_id, or the most recent if current_id is None"""
        try:
            if current_id:
                next_article = self.repo.get_next_after(current_id)
                if next_article:
                    return next_article
            
            # If no current_id or no next article, get most recent
            return self.repo.get_most_recent()
            
        except Exception as e:
            logger.error(f"Error getting next article: {str(e)}")
            return None
    
    def get_article_by_id(self, article_id: int) -> Optional[Article]:
        """Get article by ID with conversations"""
        try:
            return self.repo.get_by_id(article_id)
        except Exception as e:
            logger.error(f"Error getting article by ID: {str(e)}")
            return None
    
    def get_articles_by_tag(self, tag_name: str, limit: int = 10) -> List[Article]:
        """Get articles by tag"""
        try:
            return self.repo.get_by_tag(tag_name, limit)
        except Exception as e:
            logger.error(f"Error getting articles by tag: {str(e)}")
            return []
    
    def get_recent_articles(self, limit: int = 5) -> List[Article]:
        """Get most recent articles"""
        try:
            return self.repo.get_recent(limit)
        except Exception as e:
            logger.error(f"Error getting recent articles: {str(e)}")
            return []
    
    def cache_articles(self) -> bool:
        """Cache the most recent articles for quick access"""
        try:
            articles = self.repo.get_recent(self.cache_count)
            
            # Convert to dictionaries for caching
            cached_articles = []
            for article in articles:
                tags = [tag.name for tag in article.tags]
                
                article_dict = {
                    "id": article.id,
                    "title": article.title,
                    "summary": article.summary,
                    "source_url": article.source_url,
                    "image_url": article.image_url,
                    "created_at": article.created_at.isoformat(),
                    "tags": tags
                }
                cached_articles.append(article_dict)
            
            # Store in Redis
            self.redis.setex(
                "cached_articles",
                60 * 15,  # Cache for 15 minutes
                json.dumps(cached_articles)
            )
            
            logger.info(f"Cached {len(cached_articles)} articles in Redis")
            return True
        
        except Exception as e:
            logger.error(f"Error caching articles: {str(e)}")
            return False
    
    def get_cached_articles(self) -> List[Dict[str, Any]]:
        """Get cached articles from Redis"""
        try:
            cached_data = self.redis.get("cached_articles")
            
            if cached_data:
                return json.loads(cached_data)
            
            # If not cached, cache now and return
            self.cache_articles()
            cached_data = self.redis.get("cached_articles")
            
            return json.loads(cached_data) if cached_data else []
            
        except Exception as e:
            logger.error(f"Error getting cached articles: {str(e)}")
            return []
    
    def get_popular_tags(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most popular tags with article counts"""
        try:
            return self.repo.get_popular_tags(limit)
        except Exception as e:
            logger.error(f"Error getting popular tags: {str(e)}")
            return []
