"""
Language detection filter for ingestion pipeline.

Rejects non-English content before it enters the database.
Uses langdetect library for lightweight language identification.

This module uses stdlib logging to avoid pulling in the full app
dependency chain (FastAPI, etc.), keeping it testable in isolation.
"""

import logging
import re
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

# ── Transliterated-Latin heuristic ────────────────────────────────────────────
# Common Hindi / Urdu words that appear in Latin script and slip past both
# `relevanceLanguage=en` and `langdetect`.  Two or more hits in a title is a
# strong signal that the content is not English.
_TRANSLITERATED_WORDS = {
    "kya",
    "hai",
    "nahi",
    "mein",
    "yeh",
    "aur",
    "hoga",
    "kaise",
    "karo",
    "dekho",
    "gayi",
    "wala",
    "wali",
    "bhai",
    "dost",
    "chalayega",
    "karna",
    "karega",
    "karein",
    "sabse",
    "abhi",
    "tera",
    "teri",
    "tere",
    "matlab",
    "sach",
    "galti",
    "sasta",
    "accha",
    "bahut",
}
_TRANSLITERATED_THRESHOLD = 2  # need ≥2 matches to reject
_WORD_RE = re.compile(r"[A-Za-z]+")


def _has_transliterated_non_english(text: str) -> bool:
    """Return True if the title has ≥2 common transliterated non-English words."""
    words = {w.lower() for w in _WORD_RE.findall(text)}
    return len(words & _TRANSLITERATED_WORDS) >= _TRANSLITERATED_THRESHOLD


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


def is_english(
    title: str,
    description: Optional[str] = None,
    channel_language: Optional[str] = None,
) -> bool:
    """Check if the given text is English.

    Returns True (safe default) on detection failure — curated sources are
    overwhelmingly English, so non-English content is rejected only when
    detection succeeds with a concrete non-en language code.

    Args:
        title: Content title (required).
        description: Optional content body/description.
        channel_language: Optional BCP-47 language of the channel (e.g. "hi").
            Used as tie-breaker when langdetect is inconclusive on short text.

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

    if _has_transliterated_non_english(stripped_title):
        logger.warning("Transliterated non-English title detected: %s", stripped_title[:100])
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
        # langdetect inconclusive (text too short) — fall back to channel
        # language metadata when available.
        if channel_language and channel_language != "en":
            logger.warning(
                "Short text inconclusive, channel_language=%s: %s",
                channel_language,
                stripped_title[:100],
            )
            return False
        return True

    if is_non_english(lang):
        logger.warning(
            "Non-English content detected (lang=%s): %s",
            lang,
            title[:100],
        )
        return False

    return True
