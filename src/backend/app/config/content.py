"""
Content extraction configuration.

Defines topics, entities, and extraction patterns for content processing.
"""

from typing import Set


# Common tech topics for extraction
# Used to tag content for filtering and personalization
TECH_TOPICS: Set[str] = {
    # AI & ML
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "llm", "gpt", "chatgpt", "claude", "openai", "anthropic",
    "neural network", "nlp", "computer vision",
    
    # Companies
    "google", "apple", "microsoft", "meta", "amazon", "nvidia", "tesla",
    "intel", "amd", "qualcomm", "samsung", "ibm",
    
    # Products & Platforms
    "iphone", "android", "ios", "macos", "windows", "linux",
    "chrome", "safari", "firefox", "edge",
    
    # Security
    "cybersecurity", "security", "privacy", "hacking", "breach",
    "encryption", "malware", "ransomware", "vulnerability",
    
    # Crypto & Web3
    "blockchain", "crypto", "bitcoin", "ethereum", "web3", "nft",
    "defi", "cryptocurrency",
    
    # Business
    "startup", "vc", "funding", "acquisition", "ipo", "valuation",
    "series a", "series b", "unicorn",
    
    # Hardware
    "robotics", "automation", "autonomous", "self-driving",
    "semiconductor", "chip", "cpu", "gpu", "processor",
    
    # XR & Spatial
    "ar", "vr", "metaverse", "mixed reality", "vision pro",
    "augmented reality", "virtual reality",
    
    # Infrastructure
    "5g", "6g", "networking", "cloud", "aws", "azure", "gcp",
    "kubernetes", "docker", "serverless",
    
    # Emerging Tech
    "quantum", "computing", "quantum computing",
    
    # Software Development
    "software", "saas", "api", "developer", "programming",
    "open source", "github", "devops",
    
    # Consumer Tech
    "smartphone", "laptop", "wearable", "smartwatch", "tablet",
    "headphones", "earbuds",
    
    # Gaming
    "gaming", "esports", "console", "playstation", "xbox", "nintendo",
    "steam", "game streaming",
    
    # Media & Entertainment
    "streaming", "netflix", "spotify", "youtube", "tiktok",
    "podcast", "content creation",
    
    # Social
    "social media", "twitter", "instagram", "linkedin", "reddit",
    "threads", "mastodon", "bluesky",
    
    # Industry Verticals
    "ecommerce", "fintech", "edtech", "healthtech", "biotech",
    "proptech", "agtech", "cleantech",
    
    # Space
    "space", "spacex", "nasa", "satellite", "rocket",
    "starlink", "blue origin",
}


# Named entities (companies, products, people)
# Used for entity extraction and clustering
TECH_ENTITIES: Set[str] = {
    # Major Companies
    "apple", "google", "microsoft", "meta", "amazon", "nvidia", "tesla",
    "openai", "anthropic", "deepmind", "intel", "amd", "qualcomm",
    "samsung", "sony", "lg", "huawei", "xiaomi", "bytedance",
    "netflix", "spotify", "uber", "airbnb", "stripe", "coinbase",
    "salesforce", "adobe", "oracle", "ibm", "cisco", "vmware",
    "snap", "pinterest", "twitter", "reddit", "discord",
    "palantir", "snowflake", "databricks", "figma", "notion",
    
    # Products
    "iphone", "ipad", "mac", "macbook", "airpods", "apple watch", "vision pro",
    "pixel", "android", "chrome", "chromebook", "gmail", "youtube",
    "windows", "xbox", "surface", "copilot", "bing", "teams",
    "playstation", "switch", "oculus", "quest",
    
    # AI Products
    "chatgpt", "gpt-4", "gpt-4o", "gpt-5", "claude", "gemini",
    "llama", "mistral", "dall-e", "midjourney", "stable diffusion", "sora",
    "copilot", "cursor", "v0",
    
    # Notable People
    "elon musk", "tim cook", "satya nadella", "mark zuckerberg", "sundar pichai",
    "sam altman", "jensen huang", "jeff bezos", "bill gates",
    "dario amodei", "ilya sutskever", "demis hassabis",
}


def normalize_topic(topic: str) -> str:
    """Normalize a topic string for matching."""
    return topic.lower().strip()


def normalize_entity(entity: str) -> str:
    """Normalize an entity string for matching."""
    return entity.lower().strip()


def is_known_topic(text: str) -> bool:
    """Check if text matches a known topic."""
    return normalize_topic(text) in TECH_TOPICS


def is_known_entity(text: str) -> bool:
    """Check if text matches a known entity."""
    return normalize_entity(text) in TECH_ENTITIES
