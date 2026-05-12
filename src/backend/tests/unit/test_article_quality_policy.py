"""Tests for article_quality_policy.classify_article_quality_block."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


def _item(title: str = "", source: str = "", url: str = "", manual_added: bool = False):
    return SimpleNamespace(
        title=title,
        source=source,
        source_url=url,
        canonical_url=url,
        manual_added=manual_added,
    )


def _block(title="", source="", url=""):
    from app.services.article_quality_policy import classify_article_quality_block

    return classify_article_quality_block(_item(title=title, source=source, url=url))


# ---------------------------------------------------------------------------
# Puzzle / help content (pre-existing)
# ---------------------------------------------------------------------------


def test_wordle_hints_blocked():
    assert _block(title="Today's Wordle hints and answers") is not None


def test_nyt_connections_hints_blocked():
    assert _block(title="Wordle answers help for today") is not None


def test_regular_gaming_article_not_blocked():
    assert _block(title="Wordle competitor hits 10M daily active users") is None


# ---------------------------------------------------------------------------
# Affiliate / deals patterns — true positives (should block)
# ---------------------------------------------------------------------------


def test_percent_off_blocked():
    assert _block(title="Samsung 4TB SSD 65% off at Amazon right now") is not None


def test_gift_card_blocked():
    assert _block(title="Best Buy $50 gift card deal this weekend") is not None


def test_lifetime_plan_blocked():
    assert _block(title="Get pCloud Lifetime plan for 50% off today") is not None


def test_lifetime_deal_blocked():
    assert _block(title="VPN lifetime deal — save 90%") is not None


def test_lifetime_license_blocked():
    assert _block(title="CleanMyMac lifetime license on sale") is not None


def test_coupon_blocked():
    assert _block(title="Use this coupon for 30% off NordVPN") is not None


def test_exclusive_deal_blocked():
    assert _block(title="Exclusive deal: 1Password at 50% off for students") is not None


def test_app_deals_blocked():
    assert _block(title="Android app deals and freebies this week") is not None


def test_deals_prefix_colon_blocked():
    assert _block(title="Deals: Best earbuds under $50") is not None


def test_deals_prefix_dash_blocked():
    assert _block(title="Deal — save 40% on Roborock S8") is not None


def test_monday_best_deals_blocked():
    assert _block(title="Monday's best tech deals: AirPods, SSDs and more") is not None


def test_best_gaming_deals_blocked():
    assert _block(title="Best gaming deals this week") is not None


def test_freebies_blocked():
    assert _block(title="Weekly app deals and freebies — May 12") is not None


def test_promo_code_blocked():
    assert _block(title="ExpressVPN promo code: get 3 months free") is not None


# ---------------------------------------------------------------------------
# Source blocklist — true positives
# ---------------------------------------------------------------------------


def test_9to5toys_blocked():
    assert _block(source="9To5Toys", title="iPhone 16 Pro leather case sale") is not None


def test_9to5toys_lowercase_blocked():
    assert _block(source="9to5toys", title="Apple Watch deals this weekend") is not None


# ---------------------------------------------------------------------------
# False positives — MUST NOT block (business news)
# ---------------------------------------------------------------------------


def test_apple_intel_chip_deal_not_blocked():
    assert _block(title="Apple and Intel have reached a deal to produce future chips") is None


def test_intel_chip_deal_not_blocked():
    assert _block(title="Intel shares soar on Apple chip deal report") is None


def test_akamai_llm_deal_not_blocked():
    assert _block(title="Akamai surges on big LLM deal as Cloudflare dims") is None


def test_nvidia_equity_ai_deals_not_blocked():
    assert _block(title="Nvidia has already committed $40B to equity AI deals this year") is None


def test_rocket_lab_deal_not_blocked():
    assert _block(title="Rocket Lab surges 30% on record-setting launch deal") is None


def test_xai_anthropic_deal_not_blocked():
    assert _block(title="We're feeling cynical about xAI's big deal with Anthropic") is None


def test_lifetime_career_not_blocked():
    assert _block(title="Software engineering may no longer be a lifetime career") is None


def test_regular_tech_article_not_blocked():
    assert _block(title="OpenAI launches GPT-5 with multimodal reasoning") is None


def test_google_deal_acquisition_not_blocked():
    assert _block(title="Google strikes deal to acquire startup for $2B") is None


# ---------------------------------------------------------------------------
# Manual override bypasses all blocks
# ---------------------------------------------------------------------------


def test_manual_added_bypasses_deal_block():
    from app.services.article_quality_policy import classify_article_quality_block

    item = _item(title="Best gaming deals this week")
    item.manual_added = True
    assert classify_article_quality_block(item) is None


def test_manual_added_bypasses_source_blocklist():
    from app.services.article_quality_policy import classify_article_quality_block

    item = _item(source="9To5Toys")
    item.manual_added = True
    assert classify_article_quality_block(item) is None


# ---------------------------------------------------------------------------
# Feature flag disabled — deal blocks must be skipped
# ---------------------------------------------------------------------------


def test_deal_suppression_flag_disabled_skips_pattern():
    with patch("app.services.article_quality_policy.settings") as mock_settings:
        mock_settings.ARTICLE_DEAL_SUPPRESSION_ENABLED = False
        result = _block(title="Best gaming deals this week")
    assert result is None


def test_deal_suppression_flag_disabled_skips_source_blocklist():
    with patch("app.services.article_quality_policy.settings") as mock_settings:
        mock_settings.ARTICLE_DEAL_SUPPRESSION_ENABLED = False
        result = _block(source="9To5Toys", title="Apple Watch sale")
    assert result is None
