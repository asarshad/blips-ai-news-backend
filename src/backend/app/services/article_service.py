
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from app.models.article import Article, Tag
from typing import List, Dict, Any, Optional
import redis
import json
from app.config import settings
import logging

logger = logging.getLogger(__name__)

class ArticleService:
    def __init__(self, db: Session, redis_client: redis.Redis):
        self.db = db
        self.redis = redis_client
        self.cache_count = settings.ARTICLE_CACHE_COUNT
    
    def get_next_article(self, current_id: int = None) -> Optional[Article]:
        """Get next article after current_id, or the most recent if current_id is None"""
        try:
            if current_id:
                # Get current article to get its timestamp
                current = self.db.query(Article).filter(Article.id == current_id).first()
                if current:
                    # Get next article by created_at
                    next_article = self.db.query(Article).filter(
                        Article.created_at < current.created_at
                    ).order_by(desc(Article.created_at)).first()
                    
                    if next_article:
                        return next_article
            
            # If no current_id or no next article, get most recent
            return self.db.query(Article).order_by(desc(Article.created_at)).first()
            
        except Exception as e:
            logger.error(f"Error getting next article: {str(e)}")
            return None
    
    def get_article_by_id(self, article_id: int) -> Optional[Article]:
        """Get article by ID with conversations"""
        try:
            article = self.db.query(Article).filter(Article.id == article_id).first()
            return article
        except Exception as e:
            logger.error(f"Error getting article by ID: {str(e)}")
            return None
    
    def get_articles_by_tag(self, tag_name: str, limit: int = 10) -> List[Article]:
        """Get articles by tag"""
        try:
            tag = self.db.query(Tag).filter(Tag.name == tag_name).first()
            if tag:
                return tag.articles[:limit]
            return []
        except Exception as e:
            logger.error(f"Error getting articles by tag: {str(e)}")
            return []
    
    def get_recent_articles(self, limit: int = 5) -> List[Article]:
        """Get most recent articles"""
        try:
            articles = self.db.query(Article).order_by(
                desc(Article.created_at)
            ).limit(limit).all()
            return articles
        except Exception as e:
            logger.error(f"Error getting recent articles: {str(e)}")
            return []
    
    def cache_articles(self) -> bool:
        """Cache the most recent articles for quick access"""
        try:
            # Get most recent articles
            articles = self.db.query(Article).order_by(
                desc(Article.created_at)
            ).limit(self.cache_count).all()
            
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
            # Get tags and their counts
            tag_counts = self.db.query(
                Tag.name, 
                func.count(Article.id).label('count')
            ).join(
                Tag.articles
            ).group_by(
                Tag.name
            ).order_by(
                desc('count')
            ).limit(limit).all()
            
            return [{"name": tag.name, "count": count} for tag, count in tag_counts]
            
        except Exception as e:
            logger.error(f"Error getting popular tags: {str(e)}")
            return []
