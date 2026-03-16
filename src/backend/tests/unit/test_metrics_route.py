from app.api.routes.metrics import _curation_mix_bucket


def test_curation_mix_bucket_treats_search_and_trending_as_discovery():
    assert _curation_mix_bucket("yt_search") == "discovery"
    assert _curation_mix_bucket("yt_trending") == "discovery"
    assert _curation_mix_bucket("discovery_seed") == "discovery"
    assert _curation_mix_bucket("yt_curated") == "curated"
