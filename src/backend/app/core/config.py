"""
Application configuration using Pydantic settings.
All environment variables and configuration options are defined here.
"""

import os
from typing import List

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    # API Settings
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "blips-ai-news"

    # Security
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")
    DEBUG_ROUTES_ENABLED: bool = os.getenv("DEBUG_ROUTES_ENABLED", "false").lower() in ("true", "1", "yes", "on")
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "")
    DOCS_ENABLED: bool = os.getenv("DOCS_ENABLED", "false").lower() in ("true", "1", "yes", "on")

    # Rate limiting
    RATE_LIMIT_DEFAULT: str = os.getenv("RATE_LIMIT_DEFAULT", "60/minute")
    RATE_LIMIT_CHAT: str = os.getenv("RATE_LIMIT_CHAT", "10/minute")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/blips")
    
    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    
    # LLM Provider Configuration
    # Supported: "openai", "mistral"
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
    
    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    # Mistral
    MISTRAL_API_KEY: str = os.getenv("MISTRAL_API_KEY", "")
    MISTRAL_MODEL: str = os.getenv("MISTRAL_MODEL", "mistral-small-latest")

    # LLM resilience / cost controls
    LLM_REQUEST_TIMEOUT: int = int(os.getenv("LLM_REQUEST_TIMEOUT", "30"))
    LLM_DAILY_COST_CEILING: float = float(os.getenv("LLM_DAILY_COST_CEILING", "5.0"))
    
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

    # Daily ingestion targets (per UTC day)
    DAILY_TARGET_ARTICLES: int = int(os.getenv("DAILY_TARGET_ARTICLES", "55"))
    DAILY_TARGET_VIDEOS: int = int(os.getenv("DAILY_TARGET_VIDEOS", "35"))
    DAILY_TARGET_REELS: int = int(os.getenv("DAILY_TARGET_REELS", "25"))

    # Ingestion durability controls
    INGESTION_ENABLED: bool = os.getenv("INGESTION_ENABLED", "true").lower() in ("true", "1", "yes", "on")
    INGESTION_CRON_DISABLED: bool = os.getenv("INGESTION_CRON_DISABLED", "false").lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    INGESTION_MAX_WORKERS: int = int(os.getenv("INGESTION_MAX_WORKERS", "1"))
    # JSON mapping: "{source_type}:{feed_name}" -> int
    INGESTION_TARGET_DEFAULTS: str = os.getenv("INGESTION_TARGET_DEFAULTS", "")
    INGESTION_POLL_SECONDS: int = int(os.getenv("INGESTION_POLL_SECONDS", "30"))
    INGESTION_LEASE_TTL_MS: int = int(os.getenv("INGESTION_LEASE_TTL_MS", "60000"))

    # Catch-up loop controls
    INGEST_UNTIL_TARGETS: bool = os.getenv("INGEST_UNTIL_TARGETS", "true").lower() in ("true", "1", "yes", "on")
    # 30 minutes max to reach full targets (not just typical)
    INGEST_CATCHUP_MAX_SECONDS: int = int(os.getenv("INGEST_CATCHUP_MAX_SECONDS", "1800"))

    # Source fetch depth (larger batches help backfill around duplicates)
    RSS_ENTRIES_PER_FEED: int = int(os.getenv("RSS_ENTRIES_PER_FEED", "50"))
    YT_VIDEOS_PER_CHANNEL: int = int(os.getenv("YT_VIDEOS_PER_CHANNEL", "30"))
    
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
    
    # ==========================================================================
    # Rolling Freshness + Reservoir Strategy
    # ==========================================================================
    # Tiered content windows:
    #   Tier A (Fresh): published_at within rolling window
    #   Tier B (Backfill): created_at within short window even if published_at older
    #   Tier C (Evergreen): older high-quality items
    
    # Articles: fast news cycle
    ARTICLES_FRESH_PUBLISHED_HOURS: int = int(os.getenv("ARTICLES_FRESH_PUBLISHED_HOURS", "36"))
    ARTICLES_BACKFILL_CREATED_HOURS: int = int(os.getenv("ARTICLES_BACKFILL_CREATED_HOURS", "24"))
    ARTICLES_EVERGREEN_MAX_DAYS: int = int(os.getenv("ARTICLES_EVERGREEN_MAX_DAYS", "14"))
    
    # Videos: medium cycle
    VIDEOS_FRESH_PUBLISHED_HOURS: int = int(os.getenv("VIDEOS_FRESH_PUBLISHED_HOURS", "72"))
    VIDEOS_BACKFILL_CREATED_HOURS: int = int(os.getenv("VIDEOS_BACKFILL_CREATED_HOURS", "48"))
    VIDEOS_EVERGREEN_MAX_DAYS: int = int(os.getenv("VIDEOS_EVERGREEN_MAX_DAYS", "30"))
    
    # Reels: long evergreen window (shorts stay relevant longer)
    REELS_FRESH_PUBLISHED_HOURS: int = int(os.getenv("REELS_FRESH_PUBLISHED_HOURS", "168"))  # 7 days
    REELS_BACKFILL_CREATED_HOURS: int = int(os.getenv("REELS_BACKFILL_CREATED_HOURS", "72"))
    REELS_EVERGREEN_MAX_DAYS: int = int(os.getenv("REELS_EVERGREEN_MAX_DAYS", "45"))
    
    # Minimum fresh counts per surface (triggers top-up if below)
    MIN_FRESH_ARTICLES: int = int(os.getenv("MIN_FRESH_ARTICLES", "30"))
    MIN_FRESH_VIDEOS: int = int(os.getenv("MIN_FRESH_VIDEOS", "25"))
    MIN_FRESH_REELS: int = int(os.getenv("MIN_FRESH_REELS", "20"))
    
    # Reservoir sizes: total inventory cached for browsing
    RESERVOIR_ARTICLES: int = int(os.getenv("RESERVOIR_ARTICLES", "200"))
    RESERVOIR_VIDEOS: int = int(os.getenv("RESERVOIR_VIDEOS", "150"))
    RESERVOIR_REELS: int = int(os.getenv("RESERVOIR_REELS", "300"))
    
    # Top-up controls
    TOPUP_LOCK_TTL_SECONDS: int = int(os.getenv("TOPUP_LOCK_TTL_SECONDS", "120"))
    TOPUP_MAX_RUNTIME_SECONDS: int = int(os.getenv("TOPUP_MAX_RUNTIME_SECONDS", "300"))
    INVENTORY_HEALTH_CACHE_TTL: int = int(os.getenv("INVENTORY_HEALTH_CACHE_TTL", "60"))
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()


def get_settings() -> Settings:
    """Get application settings singleton."""
    return settings
