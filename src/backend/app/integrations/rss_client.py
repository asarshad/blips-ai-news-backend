"""
RSS feed client.

Handles fetching and parsing RSS feeds, with support for
content extraction from linked articles.

Enhanced with role-based feed configuration for diverse,
high-quality content ingestion.
"""

import feedparser
import requests
from bs4 import BeautifulSoup
import html
import random
from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.rss_feeds import (
    FeedConfig,
    FeedRole,
    QualityTier,
    DecayProfile,
    get_enabled_feeds,
    get_feed_by_url,
    get_quality_modifier,
    get_decay_half_life,
)

logger = get_logger(__name__)


@dataclass
class FeedEntry:
    """
    Represents a parsed RSS feed entry with role metadata.
    
    Enhanced to include feed configuration metadata for
    role-based ranking and decay.
    """
    title: str
    url: str
    content: str
    image_url: str
    published_date: datetime
    # Role-based metadata
    feed_name: str = ""
    feed_role: Optional[FeedRole] = None
    quality_tier: Optional[QualityTier] = None
    decay_profile: Optional[DecayProfile] = None
    base_quality_weight: Optional[float] = None


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
    
    Enhanced with role-based feed configuration for diverse,
    balanced content ingestion. Uses the new FeedConfig system
    to track metadata through the pipeline.
    """
    
    # User agents for rotation to avoid blocking
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
    ]
    
    def __init__(self, feed_configs: Optional[List[FeedConfig]] = None):
        """
        Initialize RSS client with feed configurations.
        
        Args:
            feed_configs: List of FeedConfig objects. Defaults to enabled feeds from registry.
        """
        self.feed_configs = feed_configs or get_enabled_feeds()
        # Build URL -> config lookup
        self._config_by_url = {f.url: f for f in self.feed_configs}
    
    def fetch_all_feeds(self, entries_per_feed: int = 10) -> List[FeedEntry]:
        """
        Fetch entries from all configured feeds with role metadata.
        
        Args:
            entries_per_feed: Maximum entries to fetch per feed
            
        Returns:
            List of FeedEntry objects with role metadata
        """
        entries = []
        entries_by_role: Dict[str, int] = {}
        
        for feed_config in self.feed_configs:
            try:
                # Use feed's daily_cap as max entries
                max_entries = min(entries_per_feed, feed_config.daily_cap * 3)
                feed_entries = self.fetch_feed(feed_config.url, max_entries)
                
                # Attach role metadata to entries
                for entry in feed_entries:
                    entry.feed_name = feed_config.name
                    entry.feed_role = feed_config.role
                    entry.quality_tier = feed_config.quality_tier
                    entry.decay_profile = feed_config.decay_profile
                    entry.base_quality_weight = feed_config.base_quality_weight
                
                entries.extend(feed_entries)
                
                # Track by role
                role_key = feed_config.role.value
                entries_by_role[role_key] = entries_by_role.get(role_key, 0) + len(feed_entries)
                
            except Exception as e:
                logger.error(f"Error fetching feed {feed_config.name}: {str(e)}")
        
        logger.info(f"Total RSS entries fetched: {len(entries)}")
        for role, count in sorted(entries_by_role.items()):
            logger.info(f"  {role}: {count} entries")
        
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
            
            # Use requests with user-agent to avoid blocking
            headers = {"User-Agent": random.choice(self.USER_AGENTS)}
            try:
                response = requests.get(feed_url, headers=headers, timeout=15)
                response.raise_for_status()
                feed = feedparser.parse(response.content)
            except requests.RequestException as e:
                logger.warning(f"Direct fetch failed for {feed_url}, trying feedparser: {e}")
                feed = feedparser.parse(feed_url)
            
            if feed.bozo and feed.bozo_exception:
                logger.warning(f"Feed parse warning for {feed_url}: {feed.bozo_exception}")
            
            if not feed.entries:
                logger.warning(f"Feed returned 0 entries: {feed_url} (status={getattr(feed, 'status', 'unknown')})")
                return []
            
            logger.info(f"Feed {feed_url} returned {len(feed.entries)} entries")
            
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
    
    # Domains that block scraping — skip full-page extraction and rely on
    # the RSS description fallback instead.
    SCRAPE_BLOCKLIST = {"producthunt.com"}

    def _extract_article_content(self, url: str) -> str:
        """Extract article content from URL."""
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            if domain in self.SCRAPE_BLOCKLIST:
                return ""  # fall through to RSS description

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
