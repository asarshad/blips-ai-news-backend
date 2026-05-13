"""Tests for listicle_penalty in app.ranking.quality."""

from app.ranking.quality import LISTICLE_PENALTY, compute_quality_score, listicle_penalty


# ---------------------------------------------------------------------------
# listicle_penalty() — true positives (should penalise)
# ---------------------------------------------------------------------------


def test_numbered_best_penalised():
    assert listicle_penalty("10 best laptops for students") == -LISTICLE_PENALTY


def test_numbered_top_penalised():
    assert listicle_penalty("5 top reasons to switch to Linux") == -LISTICLE_PENALTY


def test_numbered_reasons_penalised():
    assert listicle_penalty("7 reasons you should use Rust") == -LISTICLE_PENALTY


def test_numbered_tips_penalised():
    assert listicle_penalty("12 tips for faster Python code") == -LISTICLE_PENALTY


def test_numbered_gadgets_penalised():
    assert listicle_penalty("9 gadgets worth buying in 2026") == -LISTICLE_PENALTY


def test_numbered_fitness_penalised():
    assert listicle_penalty("6 fitness trackers ranked") == -LISTICLE_PENALTY


def test_case_insensitive():
    assert listicle_penalty("10 Best VPN services") == -LISTICLE_PENALTY


def test_leading_whitespace_stripped():
    assert listicle_penalty("  5 top tools  ") == -LISTICLE_PENALTY


# ---------------------------------------------------------------------------
# listicle_penalty() — false positives (must NOT penalise)
# ---------------------------------------------------------------------------


def test_no_leading_number_not_penalised():
    assert listicle_penalty("Best laptops for 2026") == 0.0


def test_number_mid_title_not_penalised():
    assert listicle_penalty("Apple releases iOS 18.5 with top features") == 0.0


def test_top_500_companies_not_penalised():
    assert listicle_penalty("Fortune 500 companies betting on AI") == 0.0


def test_regular_news_not_penalised():
    assert listicle_penalty("OpenAI launches GPT-5 with multimodal reasoning") == 0.0


def test_empty_title_returns_zero():
    assert listicle_penalty("") == 0.0


def test_none_equivalent_returns_zero():
    assert listicle_penalty("") == 0.0


# ---------------------------------------------------------------------------
# compute_quality_score() — penalty lowers final score
# ---------------------------------------------------------------------------


def test_listicle_title_lowers_quality_score():
    normal = compute_quality_score(source="TechCrunch", title="OpenAI launches GPT-5")
    listicle = compute_quality_score(source="TechCrunch", title="10 best AI tools in 2026")
    assert listicle < normal
    assert abs(normal - listicle - LISTICLE_PENALTY) < 0.01


def test_quality_score_never_negative():
    score = compute_quality_score(source="unknown_blog", title="5 best tips for productivity")
    assert score >= 0.0
