from app.config.source_tiering import (
    DomainTier,
    get_domain_policy,
    get_domain_tier,
    is_allowed_domain,
    normalize_domain,
    registered_domain,
)


def test_domain_tier_core_for_premium_publishers():
    assert get_domain_tier("https://techcrunch.com/startups") == DomainTier.CORE
    assert get_domain_tier("https://news.theverge.com/story") == DomainTier.CORE


def test_domain_tier_rotation_for_secondary_publishers():
    assert get_domain_tier("https://9to5mac.com/post") == DomainTier.ROTATION
    assert get_domain_tier("https://blog.figma.com/post") == DomainTier.ROTATION


def test_domain_tier_rotation_for_tldr_shortlist_domains():
    # Not manually listed in CORE/ROTATION sets, but present in TLDR shortlist.
    assert get_domain_tier("https://uxdesign.cc/story") == DomainTier.ROTATION


def test_domain_tier_discovery_for_curated_long_tail():
    """Explicitly listed DISCOVERY_DOMAINS are still allowed."""
    assert get_domain_tier("https://speedrun.substack.com/p/issue") == DomainTier.DISCOVERY
    assert get_domain_tier("https://latent.space/episode/123") == DomainTier.DISCOVERY


def test_domain_tier_unknown_for_arbitrary_domains():
    """Domains not in any curated list fall to UNKNOWN, not DISCOVERY."""
    assert get_domain_tier("https://example.com/post") == DomainTier.UNKNOWN
    assert get_domain_tier("https://metalevel.at/blog/prolog-coding-horror") == DomainTier.UNKNOWN
    assert get_domain_tier("https://jank-lang.org/blog/custom-ir") == DomainTier.UNKNOWN
    # Tech-hint domain names still get UNKNOWN — "dev" in domain doesn't mean curated
    assert get_domain_tier("https://some-dev-blog.io/post") == DomainTier.UNKNOWN


def test_domain_tier_blocked_for_non_editorial_sources():
    assert get_domain_tier("https://x.com/some/post") == DomainTier.BLOCKED
    assert get_domain_tier("https://advertise.tldr.tech/case-study") == DomainTier.BLOCKED


def test_policy_channel_rules():
    discovery = get_domain_policy("https://speedrun.substack.com/p/issue")
    assert discovery.tier == DomainTier.DISCOVERY
    assert discovery.allow_signal_ingest is True
    assert discovery.allow_direct_ingest is False

    assert is_allowed_domain("https://speedrun.substack.com/p/issue", channel="signal") is True
    assert is_allowed_domain("https://speedrun.substack.com/p/issue", channel="direct") is False
    assert is_allowed_domain("https://x.com/some/post", channel="signal") is False


def test_unknown_domains_blocked_from_signal_ingestion():
    """The fix for HN signal-path ingestion of personal blogs (metalevel.at, jank-lang.org)."""
    assert (
        is_allowed_domain("https://metalevel.at/blog/prolog-coding-horror", channel="signal")
        is False
    )
    assert is_allowed_domain("https://jank-lang.org/blog/custom-ir", channel="signal") is False
    assert is_allowed_domain("https://some-random-blog.com/post", channel="signal") is False
    # Curated CORE/ROTATION/DISCOVERY still pass
    assert is_allowed_domain("https://techcrunch.com/article", channel="signal") is True
    assert is_allowed_domain("https://latent.space/episode/123", channel="signal") is True


def test_normalization_helpers():
    assert normalize_domain("HTTPS://WWW.BLOG.GOOGLE/path") == "blog.google"
    assert registered_domain("news.designweek.co.uk") == "designweek.co.uk"
