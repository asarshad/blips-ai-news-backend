"""Unit tests for the language detection filter."""

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

# Load language_filter directly to avoid the heavy app.ingestion.__init__ chain
_filter_path = os.path.join(
    os.path.dirname(__file__),
    os.pardir,
    os.pardir,
    os.pardir,
    "app",
    "ingestion",
    "language_filter.py",
)
_spec = importlib.util.spec_from_file_location("language_filter", os.path.abspath(_filter_path))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["language_filter"] = _mod
_spec.loader.exec_module(_mod)
is_english = _mod.is_english
detect_language = _mod.detect_language
_MIN_DETECT_LENGTH = _mod._MIN_DETECT_LENGTH


class TestIsEnglish:
    """Tests for the is_english() function."""

    def test_english_title_returns_true(self):
        assert is_english("Apple announces new MacBook Pro with M5 chip") is True

    def test_english_title_with_description_returns_true(self):
        assert (
            is_english(
                "Google releases Android 16 beta",
                "The latest Android release brings improved notification controls and better battery management.",
            )
            is True
        )

    def test_spanish_title_returns_false(self):
        assert is_english("Las mejores aplicaciones para tu teléfono Android en 2026") is False

    def test_spanish_video_title_returns_false(self):
        assert is_english("Hardware Canucks en Español - Revisión del nuevo procesador") is False

    def test_french_title_returns_false(self):
        assert (
            is_english("Les meilleures technologies de l'année prochaine selon les experts")
            is False
        )

    def test_german_title_returns_false(self):
        assert is_english("Die besten Smartphones des Jahres im großen Vergleichstest") is False

    def test_mixed_mostly_english_returns_true(self):
        """Titles with occasional non-English words should pass if mostly English."""
        assert is_english("Tesla's new Gigafactory breaks ground in München") is True

    def test_empty_title_returns_true(self):
        """Empty strings should pass (safe default)."""
        assert is_english("") is True

    def test_none_title_returns_true(self):
        """None-equivalent empty title should pass."""
        assert is_english("   ") is True

    def test_very_short_title_returns_true(self):
        """Titles shorter than MIN_DETECT_LENGTH pass without detection."""
        short = "AI news"
        assert len(short) < _MIN_DETECT_LENGTH
        assert is_english(short) is True

    def test_title_with_description_improves_detection(self):
        """Description provides additional context for detection."""
        # A short title might be ambiguous, but description helps
        assert (
            is_english(
                "Tech Review",
                "This comprehensive review covers the latest innovations in artificial intelligence and machine learning.",
            )
            is True
        )

    def test_non_english_title_is_rejected_even_with_english_description(self):
        assert (
            is_english(
                "🛑 रोज की ये गलती आप भी करते हो?",
                "Tech tips, productivity workflow, Android settings and AI assistant shortcuts.",
            )
            is False
        )

    def test_transliterated_non_english_title_is_rejected_before_description_bias(self):
        assert (
            is_english(
                "🚨 Teri Siri ab Google chalayega — $1 BILLION ki deal ho gayi!",
                "Google AI update explained in English with tech news context and product recap.",
            )
            is False
        )

    def test_description_is_truncated_to_500_chars(self):
        """Long descriptions should be truncated for performance."""
        long_desc = "Testing language detection. " * 100  # ~2700 chars
        # Should not error out, and should detect as English
        assert is_english("Test article", long_desc) is True

    def test_langdetect_exception_returns_true_safe_default(self):
        """Verify safe default when langdetect.detect_langs raises."""
        with patch("langdetect.detect_langs", side_effect=Exception("Cannot detect")):
            assert is_english("Some ambiguous text that might fail detection") is True

    def test_langdetect_returns_english(self):
        """Verify behavior when langdetect returns 'en'."""
        mock_result = MagicMock()
        mock_result.lang = "en"
        mock_result.prob = 0.99
        with patch("langdetect.detect_langs", return_value=[mock_result]):
            assert is_english("A normal English article about technology") is True

    def test_langdetect_returns_non_english(self):
        """Verify rejection when langdetect returns non-English."""
        mock_result = MagicMock()
        mock_result.lang = "es"
        mock_result.prob = 0.95
        with patch("langdetect.detect_langs", return_value=[mock_result]):
            assert is_english("This text is detected as Spanish by mock") is False

    def test_none_description_handled(self):
        """None description should work fine."""
        assert is_english("Normal English title about tech news", None) is True

    def test_only_title_used_when_description_empty(self):
        """Empty description should not cause issues."""
        assert is_english("The latest AI developments in Silicon Valley", "") is True


class TestIsEnglishRealWorldExamples:
    """Test with real-world titles from the app's content sources."""

    def test_techcrunch_english(self):
        assert (
            is_english(
                "With co-founders leaving and an IPO looming, Elon Musk turns talk to the moon"
            )
            is True
        )

    def test_verge_english(self):
        assert (
            is_english("Google's Pixel 10 could be the most affordable flagship phone of 2026")
            is True
        )

    def test_hardware_canucks_espanol(self):
        assert (
            is_english("¡El MEJOR PC Gaming que puedes armar en 2026! Guía completa de compra")
            is False
        )

    def test_mit_tech_review_english(self):
        assert is_english("The Download: inside the QuitGPT movement, and EVs in Africa") is True

    def test_youtube_spanish_video(self):
        assert (
            is_english(
                "Revisión completa del nuevo procesador Intel Core Ultra 300",
                "En este video hacemos una revisión detallada del nuevo procesador de Intel.",
            )
            is False
        )


# ═══════════════════════════════════════════════════════════════════════════════
# detect_language
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectLanguage:
    """Tests for detect_language() which returns (lang_code, confidence)."""

    def test_english_returns_en(self):
        lang, prob = detect_language("Apple announces breakthrough AI chip for Mac")
        assert lang == "en"
        assert prob > 0.5

    def test_spanish_returns_es(self):
        lang, prob = detect_language("Las mejores aplicaciones para tu teléfono Android en 2026")
        assert lang == "es"
        assert prob > 0.0

    def test_french_returns_fr(self):
        lang, prob = detect_language("Les meilleures technologies de l'année selon les experts")
        assert lang == "fr"
        assert prob > 0.0

    def test_short_text_returns_none(self):
        short = "AI news"
        assert len(short) < _MIN_DETECT_LENGTH
        lang, prob = detect_language(short)
        assert lang is None
        assert prob == 0.0

    def test_empty_title_returns_none(self):
        lang, prob = detect_language("")
        assert lang is None
        assert prob == 0.0

    def test_exception_returns_none(self):
        with patch("langdetect.detect_langs", side_effect=Exception("fail")):
            lang, prob = detect_language("Some text long enough to trigger detection")
            assert lang is None
            assert prob == 0.0

    def test_description_improves_detection(self):
        """Passing a description alongside the title gives context."""
        lang, prob = detect_language(
            "Tech Review",
            "This article covers the latest innovations in artificial intelligence.",
        )
        assert lang == "en"

    def test_confidence_in_zero_one_range(self):
        lang, prob = detect_language("The latest developments in quantum computing")
        assert lang is not None
        assert 0.0 <= prob <= 1.0
