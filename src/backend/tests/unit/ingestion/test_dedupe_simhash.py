from app.clustering.dedupe import (
    compute_dedupe_key,
    compute_title_simhash,
    hamming_distance,
    is_near_duplicate_simhash,
)


def test_compute_dedupe_key_normalizes_title_tokens():
    k1 = compute_dedupe_key("Breaking: Apple unveils AI", "TechCrunch")
    k2 = compute_dedupe_key("breaking apple unveils ai!!!", "techcrunch")
    assert k1 == k2


def test_compute_title_simhash_is_stable_and_non_null():
    v1 = compute_title_simhash("Apple unveils new AI tools at WWDC")
    v2 = compute_title_simhash("Apple unveils new AI tools at WWDC")
    assert v1 is not None
    assert v1 == v2


def test_near_duplicate_simhash_detection():
    base = compute_title_simhash("OpenAI launches new reasoning model")
    close = int(base) ^ 0b11
    far = int(base) ^ ((1 << 8) | (1 << 20) | (1 << 35) | (1 << 50))

    assert hamming_distance(int(base), close) == 2
    assert is_near_duplicate_simhash(base, close, max_distance=3) is True
    assert is_near_duplicate_simhash(base, far, max_distance=3) is False
