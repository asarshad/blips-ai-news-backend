"""
RSS feed client.

Handles fetching and parsing RSS feeds, with support for
content extraction from linked articles.
"""

import feedparser
import requests
from bs4 import BeautifulSoup
import html
import random
from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class FeedEntry:
    """Represents a parsed RSS feed entry."""
    title: str
    url: str
    content: str
    image_url: str
    published_date: datetime


def decode_html_entities(text: str) -> str:
    """Decode HTML entities in text."""
    if not text:
        return text
    # First pass: decode HTML entities like &#8217;
    decoded = html.unescape(text)
    # Second pass: use BeautifulSoup to handle any remaining entities
    soup = BeautifulSoup(decoded, 'html.parser')
    return soup.get_text()


class RSSClient:
    """
    Client for fetching and parsing RSS feeds.
    
    Handles feed parsing, content extraction, and image discovery.
    """
    
    # User agents for rotation to avoid blocking
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
    ]
    
    def __init__(self, feed_urls: Optional[List[str]] = None):
        """
        Initialize RSS client.
        
        Args:
            feed_urls: List of RSS feed URLs. Defaults to settings.RSS_FEEDS
        """
        self.feed_urls = feed_urls or settings.RSS_FEEDS
    
    def fetch_all_feeds(self, entries_per_feed: int = 10) -> List[FeedEntry]:
        """
        Fetch entries from all configured feeds.
        
        Args:
            entries_per_feed: Maximum entries to fetch per feed
            
        Returns:
            List of FeedEntry objects
        """
        entries = []
        
        for feed_url in self.feed_urls:
            try:
                feed_entries = self.fetch_feed(feed_url, entries_per_feed)
                entries.extend(feed_entries)
            except Exception as e:
                logger.error(f"Error fetching feed {feed_url}: {str(e)}")
        
        logger.info(f"Total entries fetched: {len(entries)}")
        return entries
    
    def fetch_feed(self, feed_url: str, max_entries: int = 10) -> List[FeedEntry]:
        """
        Fetch entries from a single RSS feed.
        
        Args:
            feed_url: URL of the RSS feed
            max_entries: Maximum entries to return
            
        Returns:
            List of FeedEntry objects
        """
        entries = []
        
        try:
            logger.info(f"Fetching feed: {feed_url}")
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:max_entries]:
                try:
                    # Decode HTML entities in title
                    title = decode_html_entities(entry.title)
                    url = entry.link
                    
                    # Extract content - try full page extraction first, fallback to RSS description
                    content = self._extract_article_content(url)
                    if not content:
                        # Fallback: use RSS feed's description/summary
                        content = self._get_rss_description(entry)
                        if content:
                            logger.info(f"Using RSS description for article: {title}")
                        else:
                            logger.warning(f"Skipped article with no content: {title}")
                            continue
                    
                    # Get image
                    image_url = self._extract_image_url(entry, url)
                    
                    # Parse date
                    published_date = self._parse_date(entry)
                    
                    entries.append(FeedEntry(
                        title=title,
                        url=url,
                        content=content,
                        image_url=image_url,
                        published_date=published_date
                    ))
                    
                    logger.info(f"Added article: {title}")
                    
                except Exception as e:
                    logger.error(f"Error processing entry: {str(e)}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error parsing feed {feed_url}: {str(e)}")
            raise
        
        return entries
    
    def _extract_article_content(self, url: str) -> str:
        """Extract article content from URL."""
        try:
            headers = {"User-Agent": random.choice(self.USER_AGENTS)}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove non-content elements
            for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form', 'iframe']):
                element.extract()
            
            # Try different content extraction methods
            article_content = ""
            
            # 1. Look for common article containers
            article_tags = soup.select("article, .article, #article, .post-content, .entry-content, .content, .post, .story")
            
            if article_tags:
                largest_tag = max(article_tags, key=lambda tag: len(tag.get_text()))
                paragraphs = largest_tag.find_all('p')
                article_content = " ".join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50])
            else:
                # 2. Fallback to main paragraphs
                paragraphs = soup.find_all('p')
                article_content = " ".join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50])
            
            # Limit size for processing
            return article_content[:8000] if article_content else ""
            
        except Exception as e:
            logger.error(f"Error extracting content from {url}: {str(e)}")
            return ""
    
    def _get_rss_description(self, entry) -> str:
        """Extract content from RSS feed's description/summary fields."""
        content = ""
        
        # Try content:encoded first (full content in some feeds)
        if hasattr(entry, 'content') and entry.content:
            for c in entry.content:
                if c.get('value'):
                    content = c.get('value', '')
                    break
        
        # Fallback to summary/description
        if not content:
            content = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
        
        if content:
            # Strip HTML tags and clean up
            soup = BeautifulSoup(content, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)
            return text[:8000] if text else ""
        
        return ""
    
    def _extract_image_url(self, entry, article_url: str) -> str:
        """Extract featured image URL from feed entry or article page."""
        # Check media content
        if hasattr(entry, 'media_content') and entry.media_content:
            for media in entry.media_content:
                if 'url' in media:
                    return media['url']
        
        # Check media_thumbnail
        if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
            for thumb in entry.media_thumbnail:
                if 'url' in thumb:
                    return thumb['url']
        
        # Check enclosures
        if hasattr(entry, 'enclosures') and entry.enclosures:
            for enclosure in entry.enclosures:
                if hasattr(enclosure, 'url') and hasattr(enclosure, 'type'):
                    if enclosure.type and enclosure.type.startswith('image'):
                        return enclosure.url
        
        # Check summary/content for images
        if hasattr(entry, 'summary') and entry.summary:
            soup = BeautifulSoup(entry.summary, 'html.parser')
            img_tag = soup.find('img')
            if img_tag and img_tag.get('src'):
                return img_tag['src']
        
        # Try Open Graph image from article page
        return self._fetch_og_image(article_url)
    
    def _fetch_og_image(self, url: str) -> str:
        """Fetch Open Graph image from article page."""
        try:
            headers = {"User-Agent": random.choice(self.USER_AGENTS)}
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
            
            return ""
            
        except Exception as e:
            logger.debug(f"Error fetching OG image from {url}: {e}")
            return ""
    
    def _parse_date(self, entry) -> datetime:
        """Parse and normalize publication date."""
        if 'published_parsed' in entry and entry.published_parsed:
            return datetime(*entry.published_parsed[:6])
        return datetime.utcnow()
