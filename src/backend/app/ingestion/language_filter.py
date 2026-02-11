"""
Language detection filter for ingestion pipeline.

Rejects non-English content before it enters the database.
Uses langdetect library for lightweight language identification.

This module uses stdlib logging to avoid pulling in the full app
dependency chain (FastAPI, etc.), keeping it testable in isolation.
"""

import logging

logger = logging.getLogger(__name__)

# Minimum text length for reliable detection.
# Very short strings (<20 chars) produce unreliable results.
_MIN_DETECT_LENGTH = 20


def is_english(title: str, description: str | None = None) -> bool:
    """Check if the given text is English.

    Concatenates title + first 500 chars of description for better accuracy.
    Returns True (safe default) on detection failure — curated sources are
    overwhelmingly English, so we only reject high-confidence non-English.

    Args:
        title: Content title (required).
        description: Optional content body/description.

    Returns:
        True if the content appears to be English or detection fails.
        False if a non-English language is detected with confidence.
    """
    if not title or not title.strip():
        return True

    text = title.strip()
    if description:
        text = f"{text} {description[:500].strip()}"

    # Too short for reliable detection — allow through
    if len(text) < _MIN_DETECT_LENGTH:
        return True

    try:
        from langdetect import detect

        lang = detect(text)
        if lang != "en":
            logger.warning(
                "Non-English content detected (lang=%s): %s",
                lang,
                title[:100],
            )
            return False
        return True
    except Exception as e:
        # Detection failure (e.g., ambiguous text) — safe default is allow
        logger.debug("Language detection failed for '%s': %s", title[:80], e)
        return True
