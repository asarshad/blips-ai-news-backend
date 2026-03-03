"""
FastAPI dependency injection functions.
Centralizes all dependency providers for routes.
"""

from typing import Generator, Optional

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


# Singleton Redis connection pool — reused across all callers.
_redis_pool: Optional[redis.ConnectionPool] = None


def _get_redis_pool() -> redis.ConnectionPool:
    """Return (and lazily create) the global Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
        )
    return _redis_pool


def get_redis() -> redis.Redis:
    """
    Redis client dependency.
    Returns a Redis client backed by a shared connection pool.

    Returns:
        redis.Redis: Connected Redis client
    """
    return redis.Redis(connection_pool=_get_redis_pool())
