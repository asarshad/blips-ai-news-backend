import fakeredis

from app.ingestion.leases import claim_lease, release_lease


def test_claim_lease_is_exclusive():
    r = fakeredis.FakeRedis()

    assert claim_lease(r, key="k", owner_token="a", ttl_ms=1000) is True
    assert claim_lease(r, key="k", owner_token="b", ttl_ms=1000) is False


def test_release_lease_requires_matching_token():
    r = fakeredis.FakeRedis()

    assert claim_lease(r, key="k", owner_token="a", ttl_ms=1000) is True

    # Wrong token can't release
    assert release_lease(r, key="k", owner_token="b") is False
    assert r.get("k") == b"a"

    # Correct token releases
    assert release_lease(r, key="k", owner_token="a") is True
    assert r.get("k") is None
