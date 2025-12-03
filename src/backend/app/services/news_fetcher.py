
import feedparser
import requests
from bs4 import BeautifulSoup
import html
from app.config import settings
from app.models.article import Article, Tag
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import logging
from datetime import datetime, timedelta
import random

logger = logging.getLogger(__name__)


def decode_html_entities(text: str) -> str:
    """Decode HTML entities in text"""
    if not text:
        return text
    # First pass: decode HTML entities like &#8217;
    decoded = html.unescape(text)
    # Second pass: use BeautifulSoup to handle any remaining entities
    soup = BeautifulSoup(decoded, 'html.parser')
    return soup.get_text()


class NewsFetcher:
    def __init__(self, db: Session):
        self.db = db
        self.rss_feeds = settings.RSS_FEEDS
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
        ]

    def fetch_latest_articles(self) -> List[Dict[str, Any]]:
        """Fetch latest articles from RSS feeds"""
        articles = []
        
        for feed_url in self.rss_feeds:
            try:
                logger.info(f"Fetching feed: {feed_url}")
                feed = feedparser.parse(feed_url)
                
                for entry in feed.entries[:10]:  # Get top 10 from each feed
                    # Check if article already exists in database
                    existing = self.db.query(Article).filter(Article.source_url == entry.link).first()
                    if existing:
                        continue
                    
                    # Decode HTML entities in title
                    title = decode_html_entities(entry.title)
                    
                    article_data = {
                        "title": title,
                        "source_url": entry.link,
                        "content": self._extract_article_content(entry.link),
                        "image_url": self._extract_image_url(entry),
                        "published_date": self._parse_date(entry)
                    }
                    
                    # Only add articles that have content
                    if article_data["content"]:
                        articles.append(article_data)
                        logger.info(f"Added article: {article_data['title']}")
                    else:
                        logger.warning(f"Skipped article with no content: {article_data['title']}")
            except Exception as e:
                logger.error(f"Error fetching feed {feed_url}: {str(e)}")
                
        logger.info(f"Total articles fetched: {len(articles)}")
        return articles
    
    def _extract_article_content(self, url: str) -> str:
        """Extract article content from URL"""
        try:
            # Rotate user agents to avoid blocking
            headers = {"User-Agent": random.choice(self.user_agents)}
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove script, style, nav, and other non-content elements
            for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form', 'iframe']):
                element.extract()
            
            # Try different content extraction methods
            article_content = ""
            
            # 1. Look for common article containers
            article_tags = soup.select("article, .article, #article, .post-content, .entry-content, .content, .post, .story")
            
            if article_tags:
                # Get the largest content block
                largest_tag = max(article_tags, key=lambda tag: len(tag.get_text()))
                paragraphs = largest_tag.find_all('p')
                article_content = " ".join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50])
            else:
                # 2. Fallback to main paragraphs
                paragraphs = soup.find_all('p')
                article_content = " ".join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50])
            
            # Limit to reasonable size for GPT processing
            return article_content[:8000] if article_content else ""
        except Exception as e:
            logger.error(f"Error extracting content from {url}: {str(e)}")
            return ""
    
    def _extract_image_url(self, entry) -> str:
        """Extract featured image URL from feed entry"""
        # Check if media content is available
        if hasattr(entry, 'media_content') and entry.media_content:
            for media in entry.media_content:
                if 'url' in media:
                    return media['url']
        
        # Check media_thumbnail (common in many feeds)
        if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
            for thumb in entry.media_thumbnail:
                if 'url' in thumb:
                    return thumb['url']
        
        # Check for enclosures (common in RSS)
        if hasattr(entry, 'enclosures') and entry.enclosures:
            for enclosure in entry.enclosures:
                if hasattr(enclosure, 'url') and hasattr(enclosure, 'type'):
                    if enclosure.type and enclosure.type.startswith('image'):
                        return enclosure.url
        
        # Check for image in summary/content
        if hasattr(entry, 'summary') and entry.summary:
            soup = BeautifulSoup(entry.summary, 'html.parser')
            img_tag = soup.find('img')
            if img_tag and img_tag.get('src'):
                return img_tag['src']
        
        # Check content field
        if hasattr(entry, 'content') and entry.content:
            for content_item in entry.content:
                if 'value' in content_item:
                    soup = BeautifulSoup(content_item['value'], 'html.parser')
                    img_tag = soup.find('img')
                    if img_tag and img_tag.get('src'):
                        return img_tag['src']
        
        # Try to fetch from the article page itself
        try:
            image_url = self._fetch_og_image(entry.link)
            if image_url:
                return image_url
        except Exception as e:
            logger.debug(f"Could not fetch OG image: {e}")
        
        return ""
    
    def _fetch_og_image(self, url: str) -> str:
        """Fetch Open Graph image from article page"""
        try:
            headers = {"User-Agent": random.choice(self.user_agents)}
            response = requests.get(url, headers=headers, timeout=5)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Try Open Graph image
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                return og_image['content']
            
            # Try Twitter card image
            twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
            if twitter_image and twitter_image.get('content'):
                return twitter_image['content']
            
            # Try the first large image in the article
            article = soup.find('article') or soup.find('main') or soup
            images = article.find_all('img')
            for img in images:
                src = img.get('src') or img.get('data-src')
                if src and not any(x in src.lower() for x in ['logo', 'icon', 'avatar', 'button', 'pixel']):
                    # Prefer larger images
                    width = img.get('width')
                    if width and int(width) >= 200:
                        return src
                    elif not width:
                        return src  # No width specified, take the first one
            
            return ""
        except Exception as e:
            logger.debug(f"Error fetching OG image from {url}: {e}")
            return ""
    
    def _parse_date(self, entry) -> datetime:
        """Parse and normalize publication date"""
        if 'published_parsed' in entry and entry.published_parsed:
            return datetime(*entry.published_parsed[:6])
        return datetime.utcnow()
