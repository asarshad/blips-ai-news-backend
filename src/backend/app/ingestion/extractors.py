"""
Content metadata extractors.

Functions for extracting topics, entities, and source information
from content text and URLs.
"""

import re
from typing import List
from urllib.parse import urlparse

from app.config.content import TECH_ENTITIES, TECH_TOPICS

# Domain to source name mapping
DOMAIN_TO_SOURCE = {
    "techcrunch.com": "TechCrunch",
    "theverge.com": "The Verge",
    "wired.com": "Wired",
    "arstechnica.com": "Ars Technica",
    "engadget.com": "Engadget",
    "mashable.com": "Mashable",
    "cnet.com": "CNET",
    "zdnet.com": "ZDNet",
    "venturebeat.com": "VentureBeat",
    "technologyreview.com": "MIT Technology Review",
    "spectrum.ieee.org": "IEEE Spectrum",
    "9to5mac.com": "9to5Mac",
    "9to5google.com": "9to5Google",
    "macrumors.com": "MacRumors",
    "androidcentral.com": "Android Central",
    "tomsguide.com": "Tom's Guide",
    "tomshardware.com": "Tom's Hardware",
    "anandtech.com": "AnandTech",
    "theinformation.com": "The Information",
    "bloomberg.com": "Bloomberg",
    "reuters.com": "Reuters",
    "bbc.com": "BBC",
    "androidauthority.com": "Android Authority",
    "theregister.com": "The Register",
    "theregister.co.uk": "The Register",
    "bleepingcomputer.com": "Bleeping Computer",
    "digitaltrends.com": "Digital Trends",
    "xda-developers.com": "XDA Developers",
    "techradar.com": "TechRadar",
    "ft.com": "Financial Times",
    "404media.co": "404 Media",
    "infoworld.com": "InfoWorld",
}


def extract_topics(title: str, content: str, max_topics: int = 5) -> List[str]:
    """
    Extract tech topics from title and content.

    Uses keyword matching against a predefined list of tech topics.

    Args:
        title: Content title
        content: Content body/summary
        max_topics: Maximum topics to return

    Returns:
        List of matched topics (lowercase)

    Example:
        >>> extract_topics("Apple announces new AI features", "...")
        ['apple', 'ai', 'artificial intelligence']
    """
    text = f"{title} {content}".lower()
    found_topics = []

    for topic in TECH_TOPICS:
        # Match whole words only
        pattern = r"\b" + re.escape(topic) + r"\b"
        if re.search(pattern, text):
            found_topics.append(topic)

    return found_topics[:max_topics]


def extract_entities(title: str, content: str, max_entities: int = 5) -> List[str]:
    """
    Extract named entities from title and content.

    Uses keyword matching against a predefined list of tech entities
    (companies, products, people).

    Args:
        title: Content title
        content: Content body/summary
        max_entities: Maximum entities to return

    Returns:
        List of matched entities (lowercase)

    Example:
        >>> extract_entities("Tim Cook unveils iPhone 16", "...")
        ['tim cook', 'apple', 'iphone']
    """
    text = f"{title} {content}".lower()
    found_entities = []

    for entity in TECH_ENTITIES:
        # Match whole words only
        pattern = r"\b" + re.escape(entity) + r"\b"
        if re.search(pattern, text):
            found_entities.append(entity)

    return found_entities[:max_entities]


def extract_source(url: str) -> str:
    """
    Extract source name from URL.

    Uses domain mapping for known sources, falls back to
    extracting and capitalizing the domain name.

    Args:
        url: Content URL

    Returns:
        Human-readable source name

    Example:
        >>> extract_source("https://techcrunch.com/2024/01/01/...")
        'TechCrunch'
    """
    if not url:
        return "Unknown"

    url_lower = url.lower()

    # Check known domains
    for domain, source in DOMAIN_TO_SOURCE.items():
        if domain in url_lower:
            return source

    # Extract domain as fallback
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        # Get first part of domain and capitalize
        return domain.split(".")[0].title()
    except Exception:
        return "Unknown"


def extract_source_from_youtube_channel(channel_name: str, url: str) -> str:
    """
    Extract source name from YouTube channel info.

    Args:
        channel_name: YouTube channel name
        url: YouTube video URL

    Returns:
        Channel name or "YouTube" as fallback
    """
    if channel_name:
        return channel_name
    return "YouTube"
