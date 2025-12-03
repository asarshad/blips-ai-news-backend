"""
Logging configuration for the application.
Centralizes all logging setup in one place.
"""

import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure application-wide logging.
    
    Args:
        level: The logging level to use (default: INFO)
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
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
