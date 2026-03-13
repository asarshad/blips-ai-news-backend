"""
Language detection filter for ingestion pipeline.

Rejects non-English content before it enters the database.
Uses langdetect library for lightweight language identification.

This module uses stdlib logging to avoid pulling in the full app
dependency chain (FastAPI, etc.), keeping it testable in isolation.
"""

import logging
import unicodedata
from typing import Optional, Tuple

# Seed langdetect for deterministic results (uses random sampling internally).
# Without this, identical text can return different languages across calls.
try:
    from langdetect import DetectorFactory

    DetectorFactory.seed = 0
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Minimum text length for reliable detection.
# langdetect recommends at least 50 chars; 30 is a pragmatic minimum that
# still catches most non-English content while avoiding false rejections on
# short-but-valid titles.
_MIN_DETECT_LENGTH = 30


def _has_non_latin_letters(text: str) -> bool:
    """Detect non-Latin scripts in titles before description text can dilute the result."""
    for char in text:
        if not char.isalpha():
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        if "LATIN" not in name:
            return True
    return False


def detect_language(title: str, description: Optional[str] = None) -> Tuple[Optional[str], float]:
    """Detect the BCP-47 language code for the given text.

    Concatenates title + first 500 chars of description for better accuracy.

    Returns:
        (lang_code, confidence) where lang_code is e.g. "en", "es", "fr".
        (None, 0.0) is returned when the text is too short to detect reliably
        or when detection raises an exception.
    """
    if not title or not title.strip():
        return None, 0.0

    text = title.strip()
    if description:
        text = f"{text} {description[:500].strip()}"

    if len(text) < _MIN_DETECT_LENGTH:
        return None, 0.0

    try:
        from langdetect import detect_langs

        results = detect_langs(text)
        if results:
            top = results[0]
            return top.lang, top.prob
    except Exception as exc:
        logger.debug("Language detection failed for '%s': %s", title[:80], exc)

    return None, 0.0


def is_non_english(lang: Optional[str]) -> bool:
    """Return True if a detected language code should cause the item to be rejected.

    Centralises the rejection condition so that service.py does not re-implement
    it inline.  Always returns False for None (safe default / inconclusive).
    """
    return lang is not None and lang != "en"


def is_english(title: str, description: Optional[str] = None) -> bool:
    """Check if the given text is English.

    Returns True (safe default) on detection failure — curated sources are
    overwhelmingly English, so non-English content is rejected only when
    detection succeeds with a concrete non-en language code.

    Args:
        title: Content title (required).
        description: Optional content body/description.

    Returns:
        True if the content appears to be English or detection is inconclusive.
        False only if a non-English language is detected.
    """
    stripped_title = (title or "").strip()
    if not stripped_title:
        return True

    if _has_non_latin_letters(stripped_title):
        logger.warning("Non-Latin title detected: %s", stripped_title[:100])
        return False

    title_lang, _title_prob = detect_language(stripped_title)
    if is_non_english(title_lang):
        logger.warning(
            "Non-English title detected (lang=%s): %s",
            title_lang,
            stripped_title[:100],
        )
        return False

    lang, _prob = detect_language(stripped_title, description)

    if lang is None:
        # Too short or detection failed — allow through (safe default)
        return True

    if is_non_english(lang):
        logger.warning(
            "Non-English content detected (lang=%s): %s",
            lang,
            title[:100],
        )
        return False

    return True
