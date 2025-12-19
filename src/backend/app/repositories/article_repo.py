"""
Article repository for database operations on articles.
"""

from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, cast, Date
from datetime import datetime, timedelta

from app.repositories.base import BaseRepository
from app.models.article import Article, Tag


class ArticleRepository(BaseRepository[Article]):
    """Repository for Article model database operations."""
    
    def __init__(self, db: Session):
        super().__init__(db, Article)
    
    def get_by_url(self, source_url: str) -> Optional[Article]:
        """Get article by source URL."""
        return self.db.query(Article).filter(Article.source_url == source_url).first()
    
    def exists_by_url(self, source_url: str) -> bool:
        """Check if article exists by source URL."""
        return self.get_by_url(source_url) is not None
    
    def get_next_after(self, current_id: int) -> Optional[Article]:
        """
        Get the next article after the current one (by created_at).
        Returns the article created just before the current one.
        """
        current = self.get_by_id(current_id)
        if not current:
            return None
        
        return self.db.query(Article).filter(
            Article.created_at < current.created_at
        ).order_by(desc(Article.created_at)).first()
    
    def get_most_recent(self) -> Optional[Article]:
        """Get the most recently created article."""
        return self.db.query(Article).order_by(desc(Article.created_at)).first()
    
    def get_recent(self, limit: int = 10) -> List[Article]:
        """Get most recent articles by published date."""
        return self.db.query(Article).order_by(
            desc(Article.published_date),
            desc(Article.created_at)
        ).limit(limit).all()
    
    def get_articles_last_n_days(self, days: int = 3) -> List[Article]:
        """
        Get articles from the last N days (including today).
        Ordered by published_date descending, then by hot_score descending within each date.
        """
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).date()
        
        return self.db.query(Article).filter(
            Article.published_date >= cutoff_date
        ).order_by(
            desc(Article.published_date),             # Group by published date descending
            desc(Article.hot_score),                  # Then by hot_score within date
            desc(Article.created_at)                  # Then by exact time as tiebreaker
        ).all()
    
    def get_by_tag(self, tag_name: str, limit: int = 10) -> List[Article]:
        """Get articles by tag name."""
        tag = self.db.query(Tag).filter(Tag.name == tag_name).first()
        if tag:
            return tag.articles[:limit]
        return []
    
    def get_popular_tags(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most popular tags with article counts."""
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
        
        return [{"name": name, "count": count} for name, count in tag_counts]
    
    def get_with_placeholder_summary(self, limit: int = 10) -> List[Article]:
        """Get articles that have placeholder summaries."""
        return self.db.query(Article).filter(
            Article.summary == "Summary unavailable at the moment."
        ).limit(limit).all()
    
    def update_summary(self, article_id: int, summary: str) -> Optional[Article]:
        """Update article summary."""
        article = self.get_by_id(article_id)
        if article:
            article.summary = summary
            self.db.commit()
            self.db.refresh(article)
        return article
    
    def increment_hot_score(self, article_id: int, amount: int = 1) -> Optional[Article]:
        """Increment article hot_score by given amount."""
        article = self.get_by_id(article_id)
        if article:
            article.hot_score = (article.hot_score or 0) + amount
            self.db.commit()
            self.db.refresh(article)
        return article
    
    def add_tag(self, article: Article, tag_name: str) -> None:
        """Add a tag to an article, creating the tag if it doesn't exist."""
        tag = self.db.query(Tag).filter(Tag.name == tag_name).first()
        if not tag:
            tag = Tag(name=tag_name)
            self.db.add(tag)
        if tag not in article.tags:
            article.tags.append(tag)
        self.db.commit()
    
    def update_summary_and_tags(self, article_id: int, summary: str, tags: List[str]) -> Optional[Article]:
        """Update article summary and add tags."""
        article = self.get_by_id(article_id)
        if not article:
            return None
        
        article.summary = summary
        
        for tag_name in tags:
            tag = self.db.query(Tag).filter(Tag.name == tag_name).first()
            if not tag:
                tag = Tag(name=tag_name)
                self.db.add(tag)
            if tag not in article.tags:
                article.tags.append(tag)
        
        self.db.commit()
        self.db.refresh(article)
        return article
    
    def create_with_tags(self, article_data: Dict[str, Any]) -> Article:
        """Create an article with its associated tags."""
        from datetime import date
        
        # Extract published_date as a Date (not DateTime)
        published_date = article_data.get("published_date")
        if published_date and hasattr(published_date, 'date'):
            published_date = published_date.date()  # Convert datetime to date
        elif not published_date:
            published_date = date.today()  # Default to today if not provided
        
        # Calculate read time from content (avg 200 words per minute)
        content = article_data.get("content", "")
        word_count = len(content.split()) if content else 0
        read_time_minutes = max(1, round(word_count / 200))
        
        # Create new article
        article = Article(
            title=article_data["title"],
            source_url=article_data["source_url"],
            content=content,
            summary=article_data.get("summary", ""),
            image_url=article_data.get("image_url", ""),
            published_date=published_date,
            read_time_minutes=read_time_minutes
        )
        
        self.db.add(article)
        self.db.commit()
        self.db.refresh(article)
        
        # Add tags
        for tag_name in article_data.get("tags", []):
            tag = self.db.query(Tag).filter(Tag.name == tag_name).first()
            if not tag:
                tag = Tag(name=tag_name)
                self.db.add(tag)
            article.tags.append(tag)
        
        self.db.commit()
        self.db.refresh(article)
        return article
