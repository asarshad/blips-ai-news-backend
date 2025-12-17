
"""News fetcher service for retrieving articles from RSS feeds."""

from typing import List, Dict, Any, Optional

from app.core.logging import get_logger
from app.repositories.article_repo import ArticleRepository
from app.integrations.rss_client import RSSClient, FeedEntry

logger = get_logger(__name__)


class NewsFetcher:
    """Service for fetching news articles from RSS feeds."""
    
    def __init__(
        self, 
        article_repo: ArticleRepository,
        rss_client: Optional[RSSClient] = None
    ):
        self.article_repo = article_repo
        self.rss_client = rss_client or RSSClient()

    def fetch_latest_articles(self) -> List[Dict[str, Any]]:
        """
        Fetch latest articles from RSS feeds, filtering out existing ones.
        
        Returns:
            List of article data dictionaries for new articles only
        """
        articles = []
        feed_entries = self.rss_client.fetch_all_feeds(entries_per_feed=10)
        
        for entry in feed_entries:
            if self.article_repo.get_by_url(entry.url):
                logger.debug(f"Article already exists: {entry.title}")
                continue
            
            article_data = self._entry_to_article_data(entry)
            articles.append(article_data)
            logger.info(f"Added new article: {entry.title}")
        
        logger.info(f"Total new articles: {len(articles)}")
        return articles
    
    def _entry_to_article_data(self, entry: FeedEntry) -> Dict[str, Any]:
        """Convert a FeedEntry to article data dictionary."""
        return {
            "title": entry.title,
            "source_url": entry.url,
            "content": entry.content,
            "image_url": entry.image_url,
            "published_date": entry.published_date
        }
