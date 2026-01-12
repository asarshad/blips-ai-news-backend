"""
Application configuration using Pydantic settings.
All environment variables and configuration options are defined here.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
import os
from dotenv import load_dotenv
from typing import List

load_dotenv()


class Settings(BaseSettings):
    # API Settings
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "blips-ai-news"
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/blips")
    
    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    
    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    
    # News sources
    RSS_FEEDS: List[str] = [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://www.wired.com/feed/rss",
        "https://feeds.arstechnica.com/arstechnica/technology-lab",
        "https://www.engadget.com/rss.xml",
        "https://www.cnet.com/rss/news/",
    ]
    
    # Quota settings
    MAX_MESSAGES_PER_DAY: int = int(os.getenv("MAX_MESSAGES_PER_DAY", "5"))
    MAX_MESSAGES_PER_ARTICLE: int = int(os.getenv("MAX_MESSAGES_PER_ARTICLE", "3"))
    
    # Scheduler settings
    NEWS_FETCH_INTERVAL_HOURS: int = int(os.getenv("NEWS_FETCH_INTERVAL_HOURS", "3"))
    NEWS_FETCH_INTERVAL_MINUTES: int = int(os.getenv("NEWS_FETCH_INTERVAL_MINUTES", "30"))
    
    # Cache settings
    ARTICLE_CACHE_COUNT: int = int(os.getenv("ARTICLE_CACHE_COUNT", "5"))
    
    # Curation system settings
    PLAYLIST_CACHE_TTL_SECONDS: int = int(os.getenv("PLAYLIST_CACHE_TTL_SECONDS", "300"))
    SESSION_SNAPSHOT_TTL_SECONDS: int = int(os.getenv("SESSION_SNAPSHOT_TTL_SECONDS", "3600"))
    DEFAULT_PLAYLIST_SIZE: int = int(os.getenv("DEFAULT_PLAYLIST_SIZE", "50"))
    MAX_CONTENT_AGE_HOURS: int = int(os.getenv("MAX_CONTENT_AGE_HOURS", "72"))
    RECENCY_HALF_LIFE_HOURS: int = int(os.getenv("RECENCY_HALF_LIFE_HOURS", "24"))
    MAX_TOPIC_DOMINANCE: float = float(os.getenv("MAX_TOPIC_DOMINANCE", "0.40"))
    PREFERENCE_DECAY_FACTOR: float = float(os.getenv("PREFERENCE_DECAY_FACTOR", "0.95"))
    
    # Personalization weights (topic/entity/source/format)
    # These control how much each preference type influences personalization score
    PERSONALIZATION_TOPIC_WEIGHT: float = float(os.getenv("PERSONALIZATION_TOPIC_WEIGHT", "0.35"))
    PERSONALIZATION_ENTITY_WEIGHT: float = float(os.getenv("PERSONALIZATION_ENTITY_WEIGHT", "0.30"))
    PERSONALIZATION_SOURCE_WEIGHT: float = float(os.getenv("PERSONALIZATION_SOURCE_WEIGHT", "0.20"))
    PERSONALIZATION_FORMAT_WEIGHT: float = float(os.getenv("PERSONALIZATION_FORMAT_WEIGHT", "0.15"))
    
    # Trend score weights
    # Engagement is capped at 20% for early-stage systems to avoid gaming
    TREND_CLUSTER_WEIGHT: float = float(os.getenv("TREND_CLUSTER_WEIGHT", "0.80"))
    TREND_ENGAGEMENT_WEIGHT: float = float(os.getenv("TREND_ENGAGEMENT_WEIGHT", "0.20"))
    
    # Clustering settings
    CLUSTER_WINDOW_HOURS: int = int(os.getenv("CLUSTER_WINDOW_HOURS", "48"))
    MIN_CLUSTER_SIMILARITY: float = float(os.getenv("MIN_CLUSTER_SIMILARITY", "0.5"))
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()


def get_settings() -> Settings:
    """Get application settings singleton."""
    return settings
