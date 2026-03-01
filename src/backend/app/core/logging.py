"""
Logging configuration for the application.
Centralizes all logging setup in one place.
"""

import logging
import os
import sys


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure application-wide logging.

    Honors the LOG_LEVEL env var (DEBUG / INFO / WARNING / ERROR).
    Falls back to the *level* parameter if the env var is absent.
    """
    env_level = os.getenv("LOG_LEVEL", "").upper().strip()
    resolved = getattr(logging, env_level, None) if env_level else None
    if resolved is None:
        resolved = level

    logging.basicConfig(
        level=resolved,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ],
        force=True,  # override any prior basicConfig call
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the given name.
    
    Args:
        name: The name for the logger (typically __name__)
        
    Returns:
        A configured logger instance
    """
    return logging.getLogger(name)
