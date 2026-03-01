"""
Application configuration using Pydantic settings.

All environment variables are resolved automatically by pydantic-settings.
Field names match env-var names (case-sensitive).  Defaults below are used
when no env var is present.
"""

from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # API Settings
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "blips-ai-news"

    # Security
    ADMIN_API_KEY: str = ""
    DEBUG_ROUTES_ENABLED: bool = False
    CORS_ORIGINS: str = ""
    DOCS_ENABLED: bool = False

    # Rate limiting
    RATE_LIMIT_DEFAULT: str = "60/minute"
    RATE_LIMIT_CHAT: str = "10/minute"

    # Alerting
    ALERT_ENABLED: bool = False
    ALERT_WEBHOOK_URL: str = ""

    # Database
    DATABASE_URL: str = "postgresql://postgres:postgres@db:5432/blips"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # LLM Provider Configuration ("openai" or "mistral")
    LLM_PROVIDER: str = "openai"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Mistral
    MISTRAL_API_KEY: str = ""
    MISTRAL_MODEL: str = "mistral-small-latest"

    # LLM resilience / cost controls
    LLM_REQUEST_TIMEOUT: int = 30
    LLM_DAILY_COST_CEILING: float = 5.0

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
    MAX_MESSAGES_PER_DAY: int = 5
    MAX_MESSAGES_PER_ARTICLE: int = 3

    # Scheduler settings
    NEWS_FETCH_INTERVAL_HOURS: int = 3
    NEWS_FETCH_INTERVAL_MINUTES: int = 30

    # Cache settings
    ARTICLE_CACHE_COUNT: int = 5

    # Curation system settings
    PLAYLIST_CACHE_TTL_SECONDS: int = 300
    SESSION_SNAPSHOT_TTL_SECONDS: int = 3600
    DEFAULT_PLAYLIST_SIZE: int = 50
    MAX_CONTENT_AGE_HOURS: int = 72
    RECENCY_HALF_LIFE_HOURS: int = 24
    MAX_TOPIC_DOMINANCE: float = 0.40
    PREFERENCE_DECAY_FACTOR: float = 0.95

    # Daily ingestion targets (per UTC day)
    DAILY_TARGET_ARTICLES: int = 55
    DAILY_TARGET_VIDEOS: int = 35
    DAILY_TARGET_REELS: int = 25

    # Ingestion durability controls
    INGESTION_ENABLED: bool = True
    INGESTION_CRON_DISABLED: bool = False
    INGESTION_MAX_WORKERS: int = 1
    # JSON mapping: "{source_type}:{feed_name}" -> int
    INGESTION_TARGET_DEFAULTS: str = ""
    INGESTION_POLL_SECONDS: int = 30
    INGESTION_LEASE_TTL_MS: int = 60000

    # Catch-up loop controls
    INGEST_UNTIL_TARGETS: bool = True
    INGEST_CATCHUP_MAX_SECONDS: int = 1800  # 30 minutes max

    # Source fetch depth
    RSS_ENTRIES_PER_FEED: int = 50
    YT_VIDEOS_PER_CHANNEL: int = 30

    # Personalization weights (topic/entity/source/format)
    PERSONALIZATION_TOPIC_WEIGHT: float = 0.35
    PERSONALIZATION_ENTITY_WEIGHT: float = 0.30
    PERSONALIZATION_SOURCE_WEIGHT: float = 0.20
    PERSONALIZATION_FORMAT_WEIGHT: float = 0.15

    # Trend score weights
    TREND_CLUSTER_WEIGHT: float = 0.80
    TREND_ENGAGEMENT_WEIGHT: float = 0.20

    # Clustering settings
    CLUSTER_WINDOW_HOURS: int = 48
    MIN_CLUSTER_SIMILARITY: float = 0.5

    # ======================================================================
    # Rolling Freshness + Reservoir Strategy
    # ======================================================================

    # Articles: fast news cycle
    ARTICLES_FRESH_PUBLISHED_HOURS: int = 36
    ARTICLES_BACKFILL_CREATED_HOURS: int = 24
    ARTICLES_EVERGREEN_MAX_DAYS: int = 14

    # Videos: medium cycle
    VIDEOS_FRESH_PUBLISHED_HOURS: int = 72
    VIDEOS_BACKFILL_CREATED_HOURS: int = 48
    VIDEOS_EVERGREEN_MAX_DAYS: int = 30

    # Reels: long evergreen window (shorts stay relevant longer)
    REELS_FRESH_PUBLISHED_HOURS: int = 168  # 7 days
    REELS_BACKFILL_CREATED_HOURS: int = 72
    REELS_EVERGREEN_MAX_DAYS: int = 45

    # Reels quality enforcement (defense-in-depth)
    REEL_MAX_DURATION_SECONDS: int = 180  # 3 minutes — anything longer is a VIDEO

    # Minimum fresh counts per surface (triggers top-up if below)
    MIN_FRESH_ARTICLES: int = 30
    MIN_FRESH_VIDEOS: int = 25
    MIN_FRESH_REELS: int = 20

    # Reservoir sizes: total inventory cached for browsing
    RESERVOIR_ARTICLES: int = 200
    RESERVOIR_VIDEOS: int = 150
    RESERVOIR_REELS: int = 300

    # ------------------------------------------------------------------
    # Ads (architecture only — no SDK, all OFF by default)
    # ------------------------------------------------------------------
    ADS_ENABLED: bool = False
    ADS_FEED_CARD_ENABLED: bool = False
    ADS_BANNER_ENABLED: bool = False
    ADS_FEED_FREQUENCY: int = 0        # 1 ad every N organic items (0 = disabled)
    ADS_CANARY_PERCENT: int = 0        # % of requests that receive ads (gradual rollout)

    # Top-up controls
    TOPUP_LOCK_TTL_SECONDS: int = 120
    TOPUP_MAX_RUNTIME_SECONDS: int = 300
    INVENTORY_HEALTH_CACHE_TTL: int = 60

    # ------------------------------------------------------------------
    # Redis TTL constants (seconds)
    # ------------------------------------------------------------------
    FEED_CACHE_TTL_SECONDS: int = 300         # Feed/playlist cache
    ITEM_CACHE_TTL_SECONDS: int = 3600        # Per-item cache (quota, sessions)
    CONFIG_CACHE_TTL_SECONDS: int = 21600     # Config / feature flags (6 hours)
    LEASE_TTL_SECONDS: int = 300              # Distributed locks / leases

    # ------------------------------------------------------------------
    # Data retention policy (days)
    # ------------------------------------------------------------------
    RETAIN_CONTENT_DAYS: int = 90
    RETAIN_INGESTION_PROGRESS_DAYS: int = 14
    RETAIN_EVENTS_DAYS: int = 30
    RETAIN_CONVERSATIONS_DAYS: int = 30
    RETAIN_USAGE_DAYS: int = 90
    RETAIN_EDITORIAL_DAYS: int = 180
    RETAIN_DEBUG_DAYS: int = 7

    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "ignore",
    }


settings = Settings()


def get_settings() -> Settings:
    """Get application settings singleton."""
    return settings
