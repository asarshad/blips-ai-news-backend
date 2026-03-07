from app.config.source_registry import (
    DEFAULT_MAX_DOMAINS,
    get_source_registry_entry,
    get_source_registry_stats,
    get_tldr_source_index,
    get_tldr_source_shortlist,
    normalize_domain,
    registered_domain,
)


def test_shortlist_is_bounded_and_non_trivial():
    shortlist = get_tldr_source_shortlist()
    assert 60 <= len(shortlist) <= DEFAULT_MAX_DOMAINS


def test_shortlist_filters_noisy_platform_domains():
    domains = {entry.domain for entry in get_tldr_source_shortlist()}
    assert "x.com" not in domains
    assert "threadreaderapp.com" not in domains
    assert "advertise.tldr.tech" not in domains


def test_shortlist_keeps_high_signal_tech_publishers():
    index = get_tldr_source_index()

    assert index["techcrunch.com"].mentions == 57
    assert index["bloomberg.com"].mentions == 55
    assert index["arstechnica.com"].mentions == 34
    assert index["theverge.com"].mentions == 7


def test_lookup_supports_urls_and_subdomain_fallback():
    direct = get_source_registry_entry("https://www.techcrunch.com/2026/03/example")
    assert direct is not None
    assert direct.domain == "techcrunch.com"

    subdomain = get_source_registry_entry("https://news.theverge.com/story")
    assert subdomain is not None
    assert subdomain.domain == "theverge.com"


def test_domain_normalization_helpers():
    assert normalize_domain("HTTPS://WWW.BLOG.GOOGLE/some/path") == "blog.google"
    assert normalize_domain("techcrunch.com") == "techcrunch.com"
    assert registered_domain("news.designweek.co.uk") == "designweek.co.uk"


def test_source_registry_stats_match_shortlist():
    stats = get_source_registry_stats()
    assert stats["snapshot_domains"] == 1351
    assert stats["shortlist_domains"] == len(get_tldr_source_shortlist())
    assert stats["shortlist_domains"] >= 60
