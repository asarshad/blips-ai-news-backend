
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
    ]
    
    # Quota settings
    MAX_MESSAGES_PER_DAY: int = int(os.getenv("MAX_MESSAGES_PER_DAY", "5"))
    MAX_MESSAGES_PER_ARTICLE: int = int(os.getenv("MAX_MESSAGES_PER_ARTICLE", "3"))
    
    # Scheduler settings
    NEWS_FETCH_INTERVAL_HOURS: int = int(os.getenv("NEWS_FETCH_INTERVAL_HOURS", "3"))
    
    # Cache settings
    ARTICLE_CACHE_COUNT: int = int(os.getenv("ARTICLE_CACHE_COUNT", "5"))
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
