
import feedparser
import requests
from bs4 import BeautifulSoup
from app.config import settings
from app.models.article import Article, Tag
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class NewsFetcher:
    def __init__(self, db: Session):
        self.db = db
        self.rss_feeds = settings.RSS_FEEDS

    def fetch_latest_articles(self) -> List[Dict[str, Any]]:
        """Fetch latest articles from RSS feeds"""
        articles = []
        
        for feed_url in self.rss_feeds:
            try:
                feed = feedparser.parse(feed_url)
                
                for entry in feed.entries[:10]:  # Get top 10 from each feed
                    # Check if article already exists in database
                    existing = self.db.query(Article).filter(Article.source_url == entry.link).first()
                    if existing:
                        continue
                        
                    article_data = {
                        "title": entry.title,
                        "source_url": entry.link,
                        "content": self._extract_article_content(entry.link),
                        "image_url": self._extract_image_url(entry),
                        "published_date": self._parse_date(entry)
                    }
                    
                    articles.append(article_data)
            except Exception as e:
                logger.error(f"Error fetching feed {feed_url}: {str(e)}")
                
        return articles
    
    def _extract_article_content(self, url: str) -> str:
        """Extract article content from URL"""
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.extract()
            
            # Extract article content - this is site-specific and might need adjustments
            article_content = ""
            
            # Try common article container classes/ids
            article_tags = soup.select("article, .article, #article, .post-content, .entry-content")
            
            if article_tags:
                article_content = article_tags[0].get_text(strip=True)
            else:
                # Fallback to main paragraphs
                paragraphs = soup.find_all('p')
                article_content = " ".join([p.get_text(strip=True) for p in paragraphs])
            
            # Limit to reasonable size
            return article_content[:10000]
        except Exception as e:
            logger.error(f"Error extracting content from {url}: {str(e)}")
            return ""
    
    def _extract_image_url(self, entry) -> str:
        """Extract featured image URL from feed entry"""
        # Check if media content is available
        if 'media_content' in entry and entry.media_content:
            for media in entry.media_content:
                if 'url' in media:
                    return media['url']
        
        # Check for enclosures (common in RSS)
        if 'enclosures' in entry and entry.enclosures:
            for enclosure in entry.enclosures:
                if 'url' in enclosure and enclosure.type and enclosure.type.startswith('image'):
                    return enclosure.url
        
        # Check for image in summary
        if 'summary' in entry:
            soup = BeautifulSoup(entry.summary, 'html.parser')
            img_tag = soup.find('img')
            if img_tag and img_tag.get('src'):
                return img_tag['src']
        
        return ""
    
    def _parse_date(self, entry) -> datetime:
        """Parse and normalize publication date"""
        if 'published_parsed' in entry and entry.published_parsed:
            return datetime(*entry.published_parsed[:6])
        return datetime.utcnow()
