"""
P5-4 Score calibration tests.

Verifies the three root-cause fixes:
  1. Source quality dict has proper coverage (no major outlet falls to default).
  2. Domain-to-source mapping returns canonical names.
  3. tech_relevance_confidence multiplier penalises low-confidence items.
  4. Full global_score ordering is meaningful (>0.20 spread).
  5. Weight redistribution: quality=0.50, trend=0.20.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config.scoring import SOURCE_QUALITY_WEIGHTS, get_source_quality, scoring_weights
from app.ingestion.extractors import extract_source
from app.ranking.global_score import compute_global_score
from app.ranking.quality import compute_quality_score
from app.ranking.recency import compute_recency_score

# ---------------------------------------------------------------------------
# Group 1: Source weight coverage
# ---------------------------------------------------------------------------


class TestSourceWeightCoverage:
    def test_the_register_above_default(self):
        assert get_source_quality("The Register") > 0.35

    def test_bloomberg_premium(self):
        assert get_source_quality("Bloomberg") >= 0.90

    def test_financial_times_premium(self):
        assert get_source_quality("Financial Times") >= 0.90

    def test_android_authority_above_threshold(self):
        assert get_source_quality("Android Authority") >= 0.70

    def test_404_media_above_threshold(self):
        assert get_source_quality("404 Media") >= 0.80

    def test_unknown_source_equals_new_default(self):
        assert get_source_quality("SomeRandomBlog") == 0.35

    def test_default_key_is_035(self):
        assert SOURCE_QUALITY_WEIGHTS["default"] == 0.35

    def test_reuters_premium(self):
        assert get_source_quality("Reuters") >= 0.88

    def test_bbc_above_default(self):
        assert get_source_quality("BBC") > 0.35

    def test_anandtech_high(self):
        assert get_source_quality("AnandTech") >= 0.85

    def test_bleeping_computer_above_threshold(self):
        assert get_source_quality("Bleeping Computer") >= 0.80

    def test_the_register_exact_value(self):
        """The Register is security/sysadmin focused — should rank near premium."""
        assert get_source_quality("The Register") >= 0.82

    def test_macrumors_above_default(self):
        assert get_source_quality("MacRumors") > 0.35

    def test_techradar_above_default(self):
        assert get_source_quality("TechRadar") > 0.35


# ---------------------------------------------------------------------------
# Group 2: Domain-to-source mapping
# ---------------------------------------------------------------------------


class TestDomainToSourceMapping:
    def test_androidauthority(self):
        assert extract_source("https://www.androidauthority.com/article-123") == "Android Authority"

    def test_theregister(self):
        assert extract_source("https://www.theregister.com/article") == "The Register"

    def test_bleepingcomputer(self):
        assert extract_source("https://www.bleepingcomputer.com/news/x") == "Bleeping Computer"

    def test_ft(self):
        assert extract_source("https://ft.com/article") == "Financial Times"

    def test_404media(self):
        assert extract_source("https://404media.co/article") == "404 Media"

    def test_theregister_co_uk(self):
        assert extract_source("https://www.theregister.co.uk/article") == "The Register"

    def test_techradar(self):
        assert extract_source("https://www.techradar.com/phones/iphone") == "TechRadar"

    def test_digitaltrends(self):
        assert extract_source("https://www.digitaltrends.com/article") == "Digital Trends"

    def test_xda_developers(self):
        assert extract_source("https://xda-developers.com/something") == "XDA Developers"

    def test_infoworld(self):
        assert extract_source("https://www.infoworld.com/article") == "InfoWorld"

    # Existing mappings should still work
    def test_bloomberg_still_mapped(self):
        assert extract_source("https://bloomberg.com/tech/article") == "Bloomberg"

    def test_techcrunch_still_mapped(self):
        assert extract_source("https://techcrunch.com/2024/article") == "TechCrunch"


# ---------------------------------------------------------------------------
# Group 3: Confidence multiplier in quality score
# ---------------------------------------------------------------------------

# Shared "full metadata" kwargs so completeness=1.0 (maximises the signal from
# source quality differences and makes penalty maths easy to reason about).
_FULL_META = {
    "title": "A long enough title for testing purposes",
    "summary": "This is a sufficiently long summary for test purposes — over fifty characters.",
    "description": "Some description text here that is long enough.",
    "image_url": "https://example.com/image.jpg",
    "topics": ["ai", "security"],
    "entities": ["apple", "google"],
}


class TestConfidenceMultiplier:
    def _score(self, source: str = "TechCrunch", confidence=None) -> float:
        return compute_quality_score(
            source=source, tech_relevance_confidence=confidence, **_FULL_META
        )

    def test_none_confidence_no_change(self):
        """None confidence should not alter the score."""
        without = self._score()
        with_none = self._score(confidence=None)
        assert without == pytest.approx(with_none)

    def test_high_confidence_no_penalty(self):
        """confidence ≥ 0.95 → no penalty."""
        no_conf = self._score()
        high_conf = self._score(confidence=0.95)
        assert high_conf == pytest.approx(no_conf)

    def test_boundary_080_no_penalty(self):
        """confidence == 0.80 → exactly at boundary, no penalty."""
        no_conf = self._score()
        at_boundary = self._score(confidence=0.80)
        assert at_boundary == pytest.approx(no_conf)

    def test_065_reduces_score(self):
        """confidence=0.65 → score should be lower than baseline."""
        baseline = self._score()
        reduced = self._score(confidence=0.65)
        assert reduced < baseline

    def test_050_further_reduces_score(self):
        """confidence=0.50 → score should be lower than confidence=0.65."""
        at_065 = self._score(confidence=0.65)
        at_050 = self._score(confidence=0.50)
        assert at_050 < at_065

    def test_score_never_negative(self):
        """Quality score must always stay ≥ 0."""
        assert self._score(confidence=0.01) >= 0.0

    def test_065_penalty_magnitude(self):
        """0.65/0.80 = 0.8125 — verify the −19% penalty is applied."""
        baseline = self._score()
        at_065 = self._score(confidence=0.65)
        expected = baseline * (0.65 / 0.80)
        assert at_065 == pytest.approx(expected, abs=1e-6)

    def test_050_penalty_magnitude(self):
        """0.50/0.80 = 0.625 — verify the −38% penalty is applied."""
        baseline = self._score()
        at_050 = self._score(confidence=0.50)
        expected = baseline * (0.50 / 0.80)
        assert at_050 == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# Group 4: Score spread validation
# ---------------------------------------------------------------------------


def _make_global_score(source: str, confidence: float) -> float:
    """Compute a global_score for a fresh (1-hour-old) article with no cluster."""
    reference_time = datetime.now(timezone.utc)
    published_at = reference_time - timedelta(hours=1)

    quality = compute_quality_score(
        source=source,
        tech_relevance_confidence=confidence,
        **_FULL_META,
    )
    recency = compute_recency_score(published_at=published_at, reference_time=reference_time)
    trend = 0.0  # no cluster, no engagement
    diversity = 0.0  # neutral

    return compute_global_score(
        quality_score=quality,
        trend_score=trend,
        recency_score=recency,
        diversity_boost=diversity,
    )


class TestScoreSpread:
    def test_ordering(self):
        """TC > Register > AA > unknown blog."""
        tc = _make_global_score("TechCrunch", confidence=0.94)
        register = _make_global_score("The Register", confidence=0.90)
        aa = _make_global_score("Android Authority", confidence=0.80)
        blog = _make_global_score("Blogspot", confidence=0.65)

        assert tc > register, f"Expected TechCrunch ({tc:.4f}) > The Register ({register:.4f})"
        assert register > aa, (
            f"Expected The Register ({register:.4f}) > Android Authority ({aa:.4f})"
        )
        assert aa > blog, f"Expected Android Authority ({aa:.4f}) > Blogspot ({blog:.4f})"

    def test_meaningful_spread(self):
        """Top-to-bottom spread must exceed 0.20 — not compressed."""
        tc = _make_global_score("TechCrunch", confidence=0.94)
        blog = _make_global_score("Blogspot", confidence=0.65)
        spread = tc - blog
        assert spread > 0.20, (
            f"Spread too small: TechCrunch={tc:.4f}, Blogspot={blog:.4f}, diff={spread:.4f}"
        )

    def test_print_scores(self, capsys):
        """Print the four scores for human verification (always passes)."""
        tc = _make_global_score("TechCrunch", confidence=0.94)
        register = _make_global_score("The Register", confidence=0.90)
        aa = _make_global_score("Android Authority", confidence=0.80)
        blog = _make_global_score("Blogspot", confidence=0.65)

        print("\n--- Group 4 global scores ---")
        print(f"  TechCrunch      (conf=0.94): {tc:.4f}")
        print(f"  The Register    (conf=0.90): {register:.4f}")
        print(f"  Android Auth    (conf=0.80): {aa:.4f}")
        print(f"  Blogspot        (conf=0.65): {blog:.4f}")
        print(f"  Spread (TC−Blog): {tc - blog:.4f}")
        # always pass
        assert True


# ---------------------------------------------------------------------------
# Group 5: Weight redistribution
# ---------------------------------------------------------------------------


class TestWeightRedistribution:
    def test_quality_weight_is_050(self):
        assert scoring_weights.quality == pytest.approx(0.50)

    def test_trend_weight_is_020(self):
        assert scoring_weights.trend == pytest.approx(0.20)

    def test_recency_weight_unchanged(self):
        assert scoring_weights.recency == pytest.approx(0.20)

    def test_diversity_weight_unchanged(self):
        assert scoring_weights.diversity == pytest.approx(0.10)

    def test_weights_sum_to_one(self):
        total = (
            scoring_weights.quality
            + scoring_weights.trend
            + scoring_weights.recency
            + scoring_weights.diversity
        )
        assert total == pytest.approx(1.0, abs=0.01)
