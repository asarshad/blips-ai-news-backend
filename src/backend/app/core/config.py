"""
Application configuration using Pydantic settings.

All environment variables are resolved automatically by pydantic-settings.
Field names match env-var names (case-sensitive).  Defaults below are used
when no env var is present.
"""

from typing import List

from pydantic_settings import BaseSettings

PINNED_OPENAI_MODEL = "gpt-5-nano"


class Settings(BaseSettings):
    # API Settings
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "blips-ai-news"

    # Environment
    ENV: str = "dev"  # "dev" or "prod"
    LOG_LEVEL: str = "INFO"  # DEBUG, INFO, WARNING, ERROR

    # Security
    ADMIN_API_KEY: str = ""
    ADMIN_SHARE_TOKEN: str = ""
    DEBUG_ROUTES_ENABLED: bool = False
    CORS_ORIGINS: str = ""
    DOCS_ENABLED: bool = False
    SESSION_AUTH_ACCESS_SECRET: str = ""
    SESSION_AUTH_ACCESS_TTL_SECONDS: int = 900
    SESSION_AUTH_REFRESH_TTL_DAYS: int = 30

    # Rate limiting
    RATE_LIMIT_DEFAULT: str = "60/minute"
    RATE_LIMIT_CHAT: str = "10/minute"
    RATE_LIMIT_SESSION_CREATE: str = "10/minute"
    RATE_LIMIT_SESSION_REFRESH: str = "20/minute"
    RATE_LIMIT_EVENTS: str = "120/minute"

    # Alerting
    ALERT_ENABLED: bool = False
    ALERT_WEBHOOK_URL: str = ""

    # Database
    DATABASE_URL: str = "postgresql://postgres:postgres@db:5432/blips"
    DB_POOL_SIZE: int = 3
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE_SECONDS: int = 1800

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_MAX_CONNECTIONS: int = 20

    # LLM Provider Configuration ("openai" or "mistral")
    LLM_PROVIDER: str = "openai"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = PINNED_OPENAI_MODEL  # legacy setting; OpenAI path ignores overrides

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
    DAILY_TARGET_ARTICLES: int = 100
    DAILY_TARGET_VIDEOS: int = 60
    DAILY_TARGET_REELS: int = 40

    # Editorial / review pipeline
    # When enabled, content that would normally enter the review queue as
    # CANDIDATE is promoted immediately instead.
    AUTO_APPROVE_REVIEW_CONTENT: bool = False

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
    YT_CURATED_LOOKBACK_HOURS: int = 168
    YT_REEL_UPLOADS_API_ENABLED: bool = False
    YT_REEL_UPLOADS_MAX_PAGES: int = 2
    YT_REEL_UPLOADS_PAGE_SIZE: int = 50
    YT_UPLOADS_PLAYLIST_CACHE_TTL_SECONDS: int = 2592000
    YOUTUBE_CURATED_ONLY: bool = False
    YOUTUBE_DISCOVERY_ENABLED: bool = True

    # Discovery signal ingest (Substack/Beehiiv lead pipeline)
    DISCOVERY_SIGNAL_ENABLED: bool = True
    DISCOVERY_SIGNAL_LIMIT: int = 25
    DISCOVERY_SIGNAL_PER_SOURCE_LIMIT: int = 5

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

    # Videos: fresh for 72 hours, then eligible for recent-ingest backfill
    VIDEOS_FRESH_PUBLISHED_HOURS: int = 72
    VIDEOS_BACKFILL_CREATED_HOURS: int = 72
    VIDEOS_EVERGREEN_MAX_DAYS: int = 30
    VIDEOS_REFRESH_PUBLISHED_HOURS: int = 36
    MIN_REFRESH_VIDEOS: int = 8

    # Reels: long evergreen window (shorts stay relevant longer)
    REELS_FRESH_PUBLISHED_HOURS: int = 168  # 7 days
    REELS_BACKFILL_CREATED_HOURS: int = 72
    REELS_EVERGREEN_MAX_DAYS: int = 45
    REELS_REFRESH_PUBLISHED_HOURS: int = 24
    MIN_REFRESH_REELS: int = 12

    # Reels quality enforcement (defense-in-depth)
    REEL_MAX_DURATION_SECONDS: int = 180  # 3 minutes — anything longer is a VIDEO

    # ------------------------------------------------------------------
    # Content extraction (Phase: extraction hardening)
    # ------------------------------------------------------------------
    EXTRACTION_ENABLED: bool = True
    EXTRACTION_CONNECT_TIMEOUT: float = 10.0
    EXTRACTION_READ_TIMEOUT: float = 20.0
    EXTRACTION_MAX_RETRIES: int = 3
    EXTRACTION_BACKOFF_BASE: float = 1.5
    EXTRACTION_DOMAIN_MIN_INTERVAL: float = 1.0  # seconds between requests to same domain
    EXTRACTION_MIN_TEXT_WORDS: int = 100  # minimum words for "good" text
    EXTRACTION_IDEAL_TEXT_WORDS: int = 300  # word count for 1.0 quality score
    ARTICLE_SUMMARY_MIN_WORDS: int = 120  # skip LLM summary for thin article text
    ARTICLE_SUMMARY_MAX_WORDS: int = 1200  # clip very long article text before summarizing
    ARTICLE_SUMMARY_MIN_OUTPUT_WORDS: int = 60  # target minimum words for generated summaries
    ARTICLE_SUMMARY_MAX_OUTPUT_WORDS: int = 70  # hard cap for generated summaries
    ARTICLE_IMAGE_LLM_FALLBACK_ENABLED: bool = True
    ARTICLE_IMAGE_LLM_MAX_INPUT_CHARS: int = 12000
    VIDEO_SUMMARY_MIN_OUTPUT_WORDS: int = 50  # softer minimum for generated video summaries
    VIDEO_SUMMARY_MAX_OUTPUT_WORDS: int = 70  # hard cap for generated video summaries
    VIDEO_TECH_CLASSIFIER_ENABLED: bool = True
    VIDEO_TECH_NONE_BLOCK_CONFIDENCE: float = 0.85
    VIDEO_TECH_MIXED_ROUNDUP_BLOCK_CONFIDENCE: float = 0.80
    SOURCE_HEALTH_DEGRADED_THRESHOLD: float = 0.3  # below this, mark source degraded

    # Minimum fresh counts per surface (triggers top-up if below)
    MIN_FRESH_ARTICLES: int = 50
    MIN_FRESH_VIDEOS: int = 60
    MIN_FRESH_REELS: int = 40

    # Reservoir sizes: total inventory cached for browsing
    RESERVOIR_ARTICLES: int = 250
    RESERVOIR_VIDEOS: int = 180
    RESERVOIR_REELS: int = 320

    # ------------------------------------------------------------------
    # Legacy backend-injected ads (kept OFF; session playlist is organic-only)
    # ------------------------------------------------------------------
    ADS_ENABLED: bool = False
    ADS_FEED_CARD_ENABLED: bool = False
    ADS_BANNER_ENABLED: bool = False
    ADS_FEED_FREQUENCY: int = 0  # 1 ad every N organic items (0 = disabled)
    ADS_CANARY_PERCENT: int = 0  # % of requests that receive ads (gradual rollout)

    # ------------------------------------------------------------------
    # Runtime mobile ads config (Redis override + env fallback)
    # ------------------------------------------------------------------
    ADS_RUNTIME_ENABLED: bool = True
    ADS_PROVIDER: str = "admob_native"
    ADS_RUNTIME_CANARY_PERCENT: int = 5
    ADS_CONFIG_TTL_SECONDS: int = 300

    ADS_ARTICLES_ENABLED: bool = True
    ADS_ARTICLES_FREQUENCY: int = 8
    ADS_ARTICLES_FIRST_SLOT_AFTER: int = 2

    ADS_VIDEOS_ENABLED: bool = True
    ADS_VIDEOS_FREQUENCY: int = 8
    ADS_VIDEOS_FIRST_SLOT_AFTER: int = 2

    ADS_REELS_ENABLED: bool = False
    ADS_REELS_FREQUENCY: int = 0
    ADS_REELS_FIRST_SLOT_AFTER: int = 0

    # ------------------------------------------------------------------
    # Runtime mobile push config (Redis override + env fallback)
    # ------------------------------------------------------------------
    PUSH_RUNTIME_ENABLED: bool = False
    PUSH_MODE: str = "manual"
    PUSH_CONFIG_TTL_SECONDS: int = 300

    # Firebase Admin service account (configure one source only)
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""
    FIREBASE_SERVICE_ACCOUNT_FILE: str = ""

    # Top-up controls
    TOPUP_LOCK_TTL_SECONDS: int = 120
    TOPUP_MAX_RUNTIME_SECONDS: int = 300
    INVENTORY_HEALTH_CACHE_TTL: int = 60

    # ------------------------------------------------------------------
    # Redis TTL constants (seconds)
    # ------------------------------------------------------------------
    FEED_CACHE_TTL_SECONDS: int = 300  # Feed/playlist cache
    ITEM_CACHE_TTL_SECONDS: int = 3600  # Per-item cache (quota, sessions)
    CONFIG_CACHE_TTL_SECONDS: int = 21600  # Config / feature flags (6 hours)
    LEASE_TTL_SECONDS: int = 300  # Distributed locks / leases

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
