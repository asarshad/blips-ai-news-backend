"""
Configuration constants for scheduled tasks.

Loaded from environment variables with sensible defaults.
"""

import os

# Maximum LLM calls per job run (prevent runaway costs)
MAX_LLM_CALLS_PER_RUN = int(os.getenv("MAX_LLM_CALLS_PER_RUN", "50"))

# Maximum items to process per job run
MAX_ITEMS_PER_RUN = int(os.getenv("MAX_ITEMS_PER_RUN", "100"))

# Rate limit delay between LLM calls (seconds)
LLM_RATE_LIMIT_DELAY = float(os.getenv("LLM_RATE_LIMIT_DELAY", "0.5"))
