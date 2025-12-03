"""
Core module - configuration, dependencies, exceptions, and logging.
"""

from app.core.config import settings
from app.core.logging import get_logger, setup_logging

__all__ = ["settings", "get_logger", "setup_logging"]
