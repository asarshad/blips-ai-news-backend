"""
Application settings and configuration.

All environment variables and configuration options are centralized here.
No hard-coded values should exist elsewhere in the codebase.

Usage:
    from app.config.settings import get_settings
    settings = get_settings()
"""

from functools import lru_cache
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field
import os
from dotenv import load_dotenv

load_dotenv()


class DatabaseSettings(BaseSettings):
    """Database connection settings."""
    
    url: str = Field(
        default="postgresql://postgres:postgres@db:5432/blips",
        description="PostgreSQL connection URL"
    )
    pool_size: int = Field(default=5, description="Connection pool size")
    max_overflow: int = Field(default=10, description="Max overflow connections")
    pool_timeout: int = Field(default=30, description="Pool timeout in seconds")
    
    class Config:
        env_prefix = "DATABASE_"


class RedisSettings(BaseSettings):
    """Redis connection settings."""
    
    url: str = Field(
        default="redis://redis:6379/0",
        description="Redis connection URL"
    )
    
    class Config:
        env_prefix = "REDIS_"


class OpenAISettings(BaseSettings):
    """OpenAI API settings."""
    
    api_key: str = Field(default="", description="OpenAI API key")
    model: str = Field(default="gpt-4o-mini", description="Default model for summarization")
    max_tokens: int = Field(default=300, description="Max tokens for summary")
    temperature: float = Field(default=0.7, description="Temperature for generation")
    
    class Config:
        env_prefix = "OPENAI_"


class SchedulerSettings(BaseSettings):
    """Background job scheduler settings."""
    
    news_fetch_interval_hours: int = Field(
        default=3, description="Hours between news fetches"
    )
    news_fetch_interval_minutes: int = Field(
        default=30, description="Minutes between news fetches"
    )
    scoring_interval_minutes: int = Field(
        default=60, description="Minutes between scoring runs"
    )
    clustering_interval_minutes: int = Field(
        default=15, description="Minutes between clustering runs"
    )
    preference_decay_interval_hours: int = Field(
        default=24, description="Hours between preference decay runs"
    )
    
    class Config:
        env_prefix = "SCHEDULER_"


class QuotaSettings(BaseSettings):
    """User quota and rate limiting settings."""
    
    max_messages_per_day: int = Field(
        default=5, description="Max AI chat messages per user per day"
    )
    max_messages_per_article: int = Field(
        default=3, description="Max AI chat messages per article"
    )
    
    class Config:
        env_prefix = "QUOTA_"


class CacheSettings(BaseSettings):
    """Cache configuration settings."""
    
    article_cache_count: int = Field(
        default=5, description="Number of articles to pre-cache"
    )
    playlist_cache_ttl_seconds: int = Field(
        default=300, description="Playlist cache TTL"
    )
    session_snapshot_ttl_seconds: int = Field(
        default=3600, description="Session snapshot TTL"
    )
    
    class Config:
        env_prefix = "CACHE_"


class Settings(BaseSettings):
    """
    Main application settings.
    
    Aggregates all sub-settings and provides application-wide configuration.
    """
    
    # API Settings
    api_v1_str: str = "/api/v1"
    project_name: str = "blips-ai-news"
    debug: bool = Field(default=False, description="Enable debug mode")
    
    # Sub-settings
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    quota: QuotaSettings = Field(default_factory=QuotaSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    
    # Legacy compatibility (deprecated - use sub-settings)
    DATABASE_URL: str = Field(default="")
    REDIS_URL: str = Field(default="")
    OPENAI_API_KEY: str = Field(default="")
    
    # Content settings
    default_playlist_size: int = Field(default=50, ge=10, le=200)
    max_content_age_hours: int = Field(default=72, ge=24, le=168)
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Bridge legacy env vars to new structure
        if self.DATABASE_URL:
            self.database.url = self.DATABASE_URL
        if self.REDIS_URL:
            self.redis.url = self.REDIS_URL
        if self.OPENAI_API_KEY:
            self.openai.api_key = self.OPENAI_API_KEY


@lru_cache()
def get_settings() -> Settings:
    """
    Get application settings singleton.
    
    Uses LRU cache to ensure settings are only loaded once.
    """
    return Settings()


# Convenience exports for backward compatibility
settings = get_settings()
