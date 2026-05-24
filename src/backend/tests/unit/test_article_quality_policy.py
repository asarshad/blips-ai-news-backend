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


def test_code_token_soup_from_low_quality_source_blocked():
    result = _block(
        source="random.blog",
        title="FooBar-CLI 2.7.1 CVE-2026-1234 Patch Breaks libssl.so for Node.js Packages",
    )

    assert result is not None
    assert result.reason == "article_code_token_soup"


def test_accessible_npm_security_story_not_blocked():
    assert (
        _block(
            source="The Hacker News",
            title="npm Adds 2FA-Gated Publishing and Package Install Controls Against Supply Chain Attacks",
        )
        is None
    )


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


# ---------------------------------------------------------------------------
# How-to / consumer tutorial suppression — true positives (should block)
# ---------------------------------------------------------------------------


def test_zdnet_how_to_blocked():
    result = _block(source="ZDNet", title="How to enable 5G on your Android phone")
    assert result is not None
    assert result.reason == "consumer_howto_article"


def test_cnet_how_to_blocked():
    assert _block(source="CNET", title="How to cancel your Netflix subscription") is not None


def test_techradar_how_to_blocked():
    assert _block(source="TechRadar", title="How to use AirDrop on iPhone") is not None


def test_toms_guide_how_to_blocked():
    assert _block(source="Tom's Guide", title="How to factory reset your iPad") is not None


def test_digital_trends_how_to_blocked():
    assert (
        _block(source="Digital Trends", title="How to download YouTube videos offline") is not None
    )


def test_zdnet_what_is_blocked():
    assert _block(source="ZDNet", title="What is a VPN and do you need one?") is not None


def test_cnet_what_is_blocked():
    assert _block(source="CNET", title="What is Wi-Fi 7?") is not None


def test_toms_guide_numbered_best_blocked():
    assert _block(source="Tom's Guide", title="10 best wireless earbuds in 2025") is not None


def test_digital_trends_numbered_top_blocked():
    assert _block(source="Digital Trends", title="5 top laptops for students this year") is not None


# ---------------------------------------------------------------------------
# How-to suppression — false positives (MUST NOT block)
# ---------------------------------------------------------------------------


def test_verge_how_to_not_blocked():
    """The Verge is not in the suppression list — 'How to' explainers are fine."""
    assert _block(source="The Verge", title="How to read Anthropic's new model spec") is None


def test_techcrunch_how_to_not_blocked():
    assert _block(source="TechCrunch", title="How to build a RAG pipeline in 2025") is None


def test_zdnet_how_google_not_blocked():
    """'How Google...' doesn't match ^how\\s+to\\b — no 'to' after 'how'."""
    assert _block(source="ZDNet", title="How Google plans to challenge OpenAI this year") is None


def test_zdnet_how_openai_not_blocked():
    assert (
        _block(source="ZDNet", title="How OpenAI's new model spec changes developer tools") is None
    )


def test_zdnet_news_headline_not_blocked():
    """Genuine news from ZDNet must pass."""
    assert _block(source="ZDNet", title="ZDNet reports Google is acquiring Wiz for $23B") is None


def test_zdnet_layoffs_not_blocked():
    assert (
        _block(source="ZDNet", title="Google announces 12,000 layoffs amid AI restructuring")
        is None
    )


def test_cnet_earnings_not_blocked():
    assert _block(source="CNET", title="Apple posts record $120B quarterly revenue") is None


def test_what_is_from_non_suppressed_source_not_blocked():
    """'What is...' from a non-suppressed source (e.g. Wired) must pass."""
    assert _block(source="Wired", title="What is the EU AI Act and why does it matter?") is None


def test_numbered_best_from_techcrunch_not_blocked():
    """Listicle from non-suppressed source must pass (listicle penalty handles score)."""
    assert _block(source="TechCrunch", title="5 best AI coding tools we tested this year") is None


# ---------------------------------------------------------------------------
# How-to suppression feature flag disabled
# ---------------------------------------------------------------------------


def test_howto_suppression_flag_disabled_skips_how_to():
    with patch("app.services.article_quality_policy.settings") as mock_settings:
        mock_settings.ARTICLE_DEAL_SUPPRESSION_ENABLED = True
        mock_settings.ARTICLE_HOWTO_SUPPRESSION_ENABLED = False
        result = _block(source="ZDNet", title="How to enable 5G on your Android phone")
    assert result is None


def test_howto_suppression_flag_disabled_skips_what_is():
    with patch("app.services.article_quality_policy.settings") as mock_settings:
        mock_settings.ARTICLE_DEAL_SUPPRESSION_ENABLED = True
        mock_settings.ARTICLE_HOWTO_SUPPRESSION_ENABLED = False
        result = _block(source="CNET", title="What is Wi-Fi 7?")
    assert result is None


# ---------------------------------------------------------------------------
# manual_added bypasses how-to block
# ---------------------------------------------------------------------------


def test_manual_added_bypasses_howto_block():
    from app.services.article_quality_policy import classify_article_quality_block

    item = _item(source="ZDNet", title="How to set up a VPN on your router")
    item.manual_added = True
    assert classify_article_quality_block(item) is None


# ---------------------------------------------------------------------------
# Niche language suppression — true positives (should block)
# ---------------------------------------------------------------------------


def test_prolog_from_personal_blog_blocked():
    """'Prolog Coding Horror' from a personal blog should be blocked."""
    result = _block(title="Prolog Coding Horror", source="metalevel.at")
    assert result is not None
    assert result.reason == "niche_language_topic"
    assert "prolog" in result.detail


def test_haskell_from_unknown_source_blocked():
    """Haskell deep-dive from an unknown source is niche."""
    result = _block(title="Haskell Performance Tips You Should Know", source="some-blog.io")
    assert result is not None
    assert result.reason == "niche_language_topic"


def test_clojure_from_infoq_blocked():
    """InfoQ is now demoted to 0.72, below the 0.80 mainstream threshold."""
    result = _block(title="Building Production Systems with Clojure", source="InfoQ")
    assert result is not None
    assert result.reason == "niche_language_topic"


def test_erlang_from_default_source_blocked():
    result = _block(title="Erlang Actors vs Go Goroutines: A Deep Dive", source="unknown-blog")
    assert result is not None
    assert result.reason == "niche_language_topic"


def test_cobol_from_non_mainstream_blocked():
    result = _block(title="COBOL Still Powers Banking — Here's Why", source="some-tech-blog")
    assert result is not None
    assert result.reason == "niche_language_topic"


def test_ocaml_from_default_source_blocked():
    result = _block(title="Why OCaml Is Perfect for Compilers", source="compiler-nerd.com")
    assert result is not None
    assert result.reason == "niche_language_topic"


def test_clojurescript_blocked():
    result = _block(title="ClojureScript vs TypeScript for Frontend Apps", source="cljs-blog.dev")
    assert result is not None
    assert result.reason == "niche_language_topic"


# ---------------------------------------------------------------------------
# Niche language suppression — false positives (MUST NOT block)
# ---------------------------------------------------------------------------


def test_prolog_from_ars_technica_not_blocked():
    """Ars Technica (quality 0.90) is above the 0.80 threshold — exempt."""
    result = _block(title="Jane Street Bets on Prolog for Internal Tooling", source="Ars Technica")
    assert result is None


def test_erlang_cve_from_mainstream_not_blocked():
    """A mainstream source covering an Erlang CVE is legitimate security news."""
    result = _block(
        title="Critical Erlang/OTP SSH Vulnerability Allows RCE", source="Bleeping Computer"
    )
    assert result is None


def test_haskell_from_techcrunch_not_blocked():
    result = _block(title="Haskell Startup Raises $10M to Build AI Compilers", source="TechCrunch")
    assert result is None


def test_mainstream_article_without_niche_lang_not_blocked():
    """Completely unrelated article must not be affected."""
    result = _block(title="OpenAI Releases GPT-5 with Extended Context", source="The Verge")
    assert result is None


def test_fortran_word_in_non_language_context():
    """'fortran' appears in title but from a mainstream source — exempt."""
    result = _block(title="Legacy Fortran Code Still Running Missiles", source="Wired")
    assert result is None


def test_niche_language_flag_disabled_skips_block():
    with patch("app.services.article_quality_policy.settings") as mock_settings:
        mock_settings.ARTICLE_DEAL_SUPPRESSION_ENABLED = True
        mock_settings.ARTICLE_HOWTO_SUPPRESSION_ENABLED = True
        mock_settings.NICHE_LANGUAGE_TOPIC_SUPPRESSION_ENABLED = False
        result = _block(title="Prolog Coding Horror", source="metalevel.at")
    assert result is None


def test_manual_added_bypasses_niche_language_block():
    from app.services.article_quality_policy import classify_article_quality_block

    item = _item(title="Prolog Coding Horror", source="metalevel.at")
    item.manual_added = True
    assert classify_article_quality_block(item) is None
