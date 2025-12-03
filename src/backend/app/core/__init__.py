"""
Core module - configuration, dependencies, exceptions, and logging.
"""

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.dependencies import get_db, get_redis

__all__ = ["settings", "get_logger", "setup_logging", "get_db", "get_redis"]
