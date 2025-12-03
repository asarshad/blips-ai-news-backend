"""
FastAPI dependency injection functions.
Centralizes all dependency providers for routes.
"""

from typing import Generator
import redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """
    Database session dependency.
    Creates a new SQLAlchemy session for each request and closes it when done.
    
    Yields:
        Session: SQLAlchemy database session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_redis() -> redis.Redis:
    """
    Redis client dependency.
    Returns a Redis client connected to the configured Redis URL.
    
    Returns:
        redis.Redis: Connected Redis client
    """
    return redis.from_url(settings.REDIS_URL)
