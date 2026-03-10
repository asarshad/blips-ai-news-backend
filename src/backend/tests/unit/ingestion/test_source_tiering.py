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
    assert get_domain_tier("https://speedrun.substack.com/p/issue") == DomainTier.DISCOVERY
    assert get_domain_tier("https://example.com/post") == DomainTier.DISCOVERY


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


def test_normalization_helpers():
    assert normalize_domain("HTTPS://WWW.BLOG.GOOGLE/path") == "blog.google"
    assert registered_domain("news.designweek.co.uk") == "designweek.co.uk"
