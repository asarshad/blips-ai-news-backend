from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.ingestion import checkpoint_defaults
from app.integrations.llm_client import LLMClient
from app.integrations.rss_feeds import DecayProfile, FeedConfig, FeedRole, QualityTier
from app.models.content import ContentItem, ContentType
from app.scheduler import tasks_major_news
from app.services.major_news_constants import (
    MAJOR_NEWS_CLASSIFIER_MIN_CONFIDENCE,
    MAJOR_NEWS_DISCOVERED_VIA,
    MAJOR_NEWS_SAME_DAY_SCORE_BOOST,
)
from app.services.promotion_service import (
    PromotionConfig,
    score_candidate,
)


def _major_feed(name: str = "CNBC Technology") -> FeedConfig:
    return FeedConfig(
        url=f"https://example.com/{name}.xml",
        name=name,
        role=FeedRole.MAJOR_NEWS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=3,
        decay_profile=DecayProfile.FAST,
    )


def test_checkpoint_defaults_exclude_major_news_from_normal_rss(monkeypatch):
    feeds = [
        _major_feed(),
        FeedConfig(
            url="https://example.com/regular.xml",
            name="Regular Tech",
            role=FeedRole.BREAKING,
            daily_cap=2,
        ),
    ]

    monkeypatch.setattr(
        checkpoint_defaults,
        "_append_youtube_defaults",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.integrations.rss_client.RSSClient",
        lambda: SimpleNamespace(feed_configs=feeds),
    )
    monkeypatch.setattr(
        "app.integrations.youtube_client.YouTubeClient",
        lambda: SimpleNamespace(channel_configs=[]),
    )

    defaults = checkpoint_defaults.build_defaults()

    assert [row.feed_name for row in defaults] == ["Regular Tech"]


def test_major_news_probe_skips_under_memory_pressure(monkeypatch):
    skips: list[dict] = []
    monkeypatch.setattr(tasks_major_news, "memory_over_soft_limit", lambda: True)
    monkeypatch.setattr(tasks_major_news, "current_worker_memory_mb", lambda: 1500.0)
    monkeypatch.setattr(tasks_major_news, "memory_soft_limit_mb", lambda: 1400)
    monkeypatch.setattr(
        tasks_major_news,
        "_record_major_news_probe_skip",
        lambda **kwargs: skips.append(kwargs),
    )

    result = tasks_major_news.run_major_news_probe_job()

    assert result["status"] == "skipped_memory"
    assert result["skip_reason"] == "memory_pressure"
    assert skips[0]["status"] == "skipped_memory"


def test_major_news_probe_respects_insert_limit(monkeypatch):
    inserted_batches: list[list[dict]] = []

    class _FakeDB:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class _FakeStateRepo:
        def __init__(self, _db):
            pass

        def get_active_cooldown(self, **_kwargs):
            return None

        def record_outcome(self, **_kwargs):
            return None

    class _FakeRSSClient:
        def __init__(self, feed_configs):
            self.feed_configs = feed_configs

        def fetch_feed(self, _url, max_entries, **_kwargs):
            return [
                SimpleNamespace(title=f"Apple story {idx}", url=f"https://example.com/{idx}")
                for idx in range(max_entries)
            ]

        def get_last_fetch_outcome(self, _url):
            return None

    monkeypatch.setattr(tasks_major_news, "memory_over_soft_limit", lambda: False)
    monkeypatch.setattr(tasks_major_news, "_ingestion_lane_recently_running", lambda: False)
    monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(tasks_major_news, "SourceFetchStateRepository", _FakeStateRepo)
    monkeypatch.setattr(tasks_major_news, "RSSClient", _FakeRSSClient)
    monkeypatch.setattr(tasks_major_news, "get_feeds_by_role", lambda _role: [_major_feed()])
    monkeypatch.setattr(
        tasks_major_news,
        "_entry_to_value",
        lambda entry, **_kwargs: {"source_url": entry.url},
    )

    def _fake_insert(_db, *, values):
        inserted_batches.append(values)
        return list(range(1, len(values) + 1))

    monkeypatch.setattr(tasks_major_news, "_insert_content_items_postgres", _fake_insert)
    monkeypatch.setattr(
        tasks_major_news, "_queue_followup_events_for_inserted_ids", lambda *_a, **_k: None
    )
    monkeypatch.setattr(tasks_major_news, "_fast_track_major_news_events", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        tasks_major_news,
        "_retro_classify_premium_breaking",
        lambda *_a, **_k: {"classified": 0, "major": 0},
    )
    monkeypatch.setenv("MAJOR_NEWS_PROBE_MAX_INSERTED", "2")
    monkeypatch.setenv("MAJOR_NEWS_PROBE_ENTRIES_PER_FEED", "5")

    result = tasks_major_news.run_major_news_probe_job()

    assert result["inserted"] == 2
    assert len(inserted_batches[0]) == 2


def test_major_news_probe_skips_entries_older_than_promotion_window(monkeypatch):
    inserted_batches: list[list[dict]] = []

    class _FakeDB:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class _FakeStateRepo:
        def __init__(self, _db):
            pass

        def get_active_cooldown(self, **_kwargs):
            return None

        def record_outcome(self, **_kwargs):
            return None

    class _FakeRSSClient:
        def __init__(self, feed_configs):
            self.feed_configs = feed_configs

        def fetch_feed(self, _url, max_entries, **_kwargs):
            return [
                SimpleNamespace(
                    title="Old Apple earnings story",
                    url="https://example.com/old",
                    published_at=datetime.utcnow() - timedelta(hours=72),
                ),
                SimpleNamespace(
                    title="Fresh Apple earnings story",
                    url="https://example.com/fresh",
                    published_at=datetime.utcnow() - timedelta(hours=2),
                ),
            ]

        def get_last_fetch_outcome(self, _url):
            return None

    monkeypatch.setattr(tasks_major_news, "memory_over_soft_limit", lambda: False)
    monkeypatch.setattr(tasks_major_news, "_ingestion_lane_recently_running", lambda: False)
    monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(tasks_major_news, "SourceFetchStateRepository", _FakeStateRepo)
    monkeypatch.setattr(tasks_major_news, "RSSClient", _FakeRSSClient)
    monkeypatch.setattr(tasks_major_news, "get_feeds_by_role", lambda _role: [_major_feed()])
    monkeypatch.setattr(
        tasks_major_news,
        "_entry_to_value",
        lambda entry, **_kwargs: {
            "title": entry.title,
            "source_url": entry.url,
            "published_at": entry.published_at,
        },
    )
    monkeypatch.setattr(
        tasks_major_news,
        "_maybe_classify_major_news_value",
        lambda value, **_kwargs: value.update({"is_major_tech_news": True}) or True,
    )

    def _fake_insert(_db, *, values):
        inserted_batches.append(values)
        return list(range(1, len(values) + 1))

    monkeypatch.setattr(tasks_major_news, "_insert_content_items_postgres", _fake_insert)
    monkeypatch.setattr(
        tasks_major_news, "_queue_followup_events_for_inserted_ids", lambda *_a, **_k: None
    )
    monkeypatch.setattr(tasks_major_news, "_fast_track_major_news_events", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        tasks_major_news,
        "_retro_classify_premium_breaking",
        lambda *_a, **_k: {"classified": 0, "major": 0},
    )
    monkeypatch.setenv("MAJOR_NEWS_PROBE_MAX_ENTRY_AGE_HOURS", "48")

    result = tasks_major_news.run_major_news_probe_job()

    assert result["inserted"] == 1
    assert result["skipped_stale"] == 1
    assert inserted_batches[0][0]["source_url"] == "https://example.com/fresh"


def test_major_news_probe_marks_only_confirmed_items_for_fast_track(monkeypatch):
    fast_tracked: list[int] = []

    class _FakeDB:
        def __init__(self):
            self._items = [SimpleNamespace(id=1, is_major_tech_news=True)]

        def query(self, model):
            assert model is ContentItem
            items = list(self._items)

            class _Query:
                def filter(self, *_args, **_kwargs):
                    return self

                def all(self):
                    return items

            return _Query()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class _FakeStateRepo:
        def __init__(self, _db):
            pass

        def get_active_cooldown(self, **_kwargs):
            return None

        def record_outcome(self, **_kwargs):
            return None

    class _FakeRSSClient:
        def __init__(self, feed_configs):
            self.feed_configs = feed_configs

        def fetch_feed(self, _url, max_entries, **_kwargs):
            return [
                SimpleNamespace(title=f"Story {idx}", url=f"https://example.com/{idx}")
                for idx in range(max_entries)
            ]

        def get_last_fetch_outcome(self, _url):
            return None

    class _FakeLLM:
        def __init__(self):
            self.calls = 0

        def is_configured(self):
            return True

        def classify_blips_tech_relevance(self, **_kwargs):
            return SimpleNamespace(
                is_blips_tech_relevant="yes",
                confidence=MAJOR_NEWS_CLASSIFIER_MIN_CONFIDENCE + 0.1,
                reason="Relevant",
            )

        def classify_major_tech_news(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                is_major_tech_news="yes" if self.calls == 1 else "no",
                confidence=0.9,
                reason="classified",
            )

    monkeypatch.setattr(tasks_major_news, "memory_over_soft_limit", lambda: False)
    monkeypatch.setattr(tasks_major_news, "_ingestion_lane_recently_running", lambda: False)
    monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(tasks_major_news, "SourceFetchStateRepository", _FakeStateRepo)
    monkeypatch.setattr(tasks_major_news, "RSSClient", _FakeRSSClient)
    monkeypatch.setattr(tasks_major_news, "LLMClient", _FakeLLM)
    monkeypatch.setattr(tasks_major_news, "get_feeds_by_role", lambda _role: [_major_feed()])
    monkeypatch.setattr(
        tasks_major_news,
        "_entry_to_value",
        lambda entry, **_kwargs: {
            "title": entry.title,
            "description": entry.title,
            "source": "CNBC Technology",
            "source_url": entry.url,
        },
    )
    monkeypatch.setattr(
        tasks_major_news, "_insert_content_items_postgres", lambda _db, *, values: [1, 2]
    )
    monkeypatch.setattr(
        tasks_major_news, "_queue_followup_events_for_inserted_ids", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        tasks_major_news,
        "_fast_track_major_news_events",
        lambda _db, *, content_item_ids, now: (
            fast_tracked.extend(content_item_ids) or len(content_item_ids)
        ),
    )
    monkeypatch.setattr(
        tasks_major_news,
        "_retro_classify_premium_breaking",
        lambda *_a, **_k: {"classified": 0, "major": 0},
    )
    monkeypatch.setenv("MAJOR_NEWS_PROBE_MAX_INSERTED", "2")
    monkeypatch.setenv("MAJOR_NEWS_PROBE_ENTRIES_PER_FEED", "2")

    result = tasks_major_news.run_major_news_probe_job()

    assert result["inserted"] == 2
    assert fast_tracked == [1]


def test_major_news_score_boost_applies_only_same_day_articles(monkeypatch):
    monkeypatch.setattr("app.services.promotion_service.compute_source_weight", lambda _source: 0.8)
    config = PromotionConfig(
        w_source=1.0,
        w_cluster=0.0,
        w_recency=0.0,
        w_clickbait=0.0,
        w_duplicate=0.0,
    )
    item = ContentItem(
        type=ContentType.ARTICLE,
        source="CNBC Technology",
        source_url="https://example.com/apple",
        published_at=datetime.utcnow(),
        title="Apple faces major antitrust lawsuit",
        is_major_tech_news=True,
        discovered_via=MAJOR_NEWS_DISCOVERED_VIA,
    )

    score = score_candidate(item, {}, config)

    assert score == round(0.8 + MAJOR_NEWS_SAME_DAY_SCORE_BOOST, 4)

    item.published_at = datetime.utcnow() - timedelta(days=1)
    assert score_candidate(item, {}, config) == 0.8


def test_fake_llm_major_news_classifier_returns_structured_json():
    result = LLMClient(provider="fake").classify_major_tech_news(
        title="Nvidia announces new chip export restrictions",
        summary="Policy change affects semiconductor supply chains.",
        source="CNBC Technology",
        url="https://example.com/nvidia",
    )

    assert result.is_major_tech_news == "yes"
    assert result.confidence > 0.8


# ---------------------------------------------------------------------------
# _retro_classify_premium_breaking tests
# ---------------------------------------------------------------------------


def _make_candidate(
    item_id: int,
    source: str = "TechCrunch",
    *,
    is_major_tech_news=None,
    tech_relevance=None,
    tech_relevance_confidence=None,
) -> SimpleNamespace:
    """Create a minimal ContentItem-like namespace for retro-classify tests."""
    return SimpleNamespace(
        id=item_id,
        title=f"Story {item_id} from {source}",
        description=f"Description {item_id}",
        summary=None,
        content_text=None,
        source=source,
        source_url=f"https://example.com/{item_id}",
        is_major_tech_news=is_major_tech_news,
        major_tech_news_confidence=None,
        major_tech_news_reason=None,
        tech_relevance=tech_relevance,
        tech_relevance_confidence=tech_relevance_confidence,
    )


class _FakeQueryChain:
    """Supports the SQLAlchemy .filter().order_by().limit().all() chain."""

    def __init__(self, items):
        self._items = items

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, _n):
        return self

    def all(self):
        return list(self._items)


def _fake_db_with_candidates(candidates):
    """Return a minimal DB stub whose .query(ContentItem) returns candidates."""
    committed = []
    rolled_back = []

    class _FakeDB:
        def query(self, _model):
            return _FakeQueryChain(candidates)

        def commit(self):
            committed.append(True)

        def rollback(self):
            rolled_back.append(True)

        def close(self):
            pass

    db = _FakeDB()
    db._committed = committed
    db._rolled_back = rolled_back
    return db


def _make_llm(*, major: bool = True, tech_relevant: bool = True):
    """Return a fake LLMClient that classifies as major (or not)."""

    class _FakeLLM:
        def is_configured(self):
            return True

        def classify_blips_tech_relevance(self, **_kwargs):
            return SimpleNamespace(
                is_blips_tech_relevant="yes" if tech_relevant else "no",
                confidence=0.9,
                reason="tech_relevant",
            )

        def classify_major_tech_news(self, **_kwargs):
            return SimpleNamespace(
                is_major_tech_news="yes" if major else "no",
                confidence=0.88,
                reason="major" if major else "not_major",
            )

    return _FakeLLM()


def test_retro_classify_empty_sources_returns_zeros():
    """Empty sources list → fast exit, no DB interaction."""
    db = _fake_db_with_candidates([])
    result = tasks_major_news._retro_classify_premium_breaking(
        db,
        _make_llm(),
        sources=[],
        max_items=10,
        lookback_hours=24,
        now=datetime.utcnow(),
    )
    assert result == {"classified": 0, "major": 0}


def test_retro_classify_no_candidates_returns_zeros():
    """No matching DB rows → returns zeros without committing."""
    db = _fake_db_with_candidates([])
    result = tasks_major_news._retro_classify_premium_breaking(
        db,
        _make_llm(),
        sources=["TechCrunch"],
        max_items=10,
        lookback_hours=24,
        now=datetime.utcnow(),
    )
    assert result == {"classified": 0, "major": 0}
    assert not db._committed


def test_retro_classify_marks_major_items(monkeypatch):
    """Items classified as major get is_major_tech_news=True and are fast-tracked."""
    fast_tracked: list[int] = []
    monkeypatch.setattr(
        tasks_major_news,
        "_fast_track_major_news_events",
        lambda db, *, content_item_ids, now: (
            fast_tracked.extend(content_item_ids) or len(content_item_ids)
        ),
    )

    item = _make_candidate(42, "TechCrunch")
    db = _fake_db_with_candidates([item])

    result = tasks_major_news._retro_classify_premium_breaking(
        db,
        _make_llm(major=True),
        sources=["TechCrunch"],
        max_items=10,
        lookback_hours=24,
        now=datetime.utcnow(),
    )

    assert result["classified"] == 1
    assert result["major"] == 1
    assert item.is_major_tech_news is True
    assert 42 in fast_tracked
    assert db._committed


def test_retro_classify_non_major_items_not_fast_tracked(monkeypatch):
    """Items classified as non-major get is_major_tech_news=False and no fast-track."""
    fast_tracked: list[int] = []
    monkeypatch.setattr(
        tasks_major_news,
        "_fast_track_major_news_events",
        lambda db, *, content_item_ids, now: (
            fast_tracked.extend(content_item_ids) or len(content_item_ids)
        ),
    )

    item = _make_candidate(7, "The Verge")
    db = _fake_db_with_candidates([item])

    result = tasks_major_news._retro_classify_premium_breaking(
        db,
        _make_llm(major=False),
        sources=["The Verge"],
        max_items=10,
        lookback_hours=24,
        now=datetime.utcnow(),
    )

    assert result["classified"] == 1
    assert result["major"] == 0
    assert item.is_major_tech_news is False
    assert fast_tracked == []


def test_retro_classify_backfills_tech_relevance_when_missing(monkeypatch):
    """tech_relevance fields are backfilled when item doesn't have them yet."""
    monkeypatch.setattr(tasks_major_news, "_fast_track_major_news_events", lambda *_a, **_k: 0)

    item = _make_candidate(99, "Ars Technica", tech_relevance=None, tech_relevance_confidence=None)
    db = _fake_db_with_candidates([item])

    tasks_major_news._retro_classify_premium_breaking(
        db,
        _make_llm(major=True, tech_relevant=True),
        sources=["Ars Technica"],
        max_items=10,
        lookback_hours=24,
        now=datetime.utcnow(),
    )

    assert item.tech_relevance == "yes"
    assert item.tech_relevance_confidence == 0.9


def test_retro_classify_does_not_overwrite_existing_tech_relevance(monkeypatch):
    """Existing tech_relevance values are preserved (not overwritten by retro pass)."""
    monkeypatch.setattr(tasks_major_news, "_fast_track_major_news_events", lambda *_a, **_k: 0)

    item = _make_candidate(55, "TechCrunch", tech_relevance="yes", tech_relevance_confidence=0.77)
    db = _fake_db_with_candidates([item])

    tasks_major_news._retro_classify_premium_breaking(
        db,
        _make_llm(major=True, tech_relevant=True),
        sources=["TechCrunch"],
        max_items=10,
        lookback_hours=24,
        now=datetime.utcnow(),
    )

    # Should remain unchanged (0.77, not overwritten with 0.9)
    assert item.tech_relevance_confidence == 0.77


def test_retro_classify_handles_commit_failure_gracefully(monkeypatch):
    """First DB commit failure → rollback, returns zeros, no crash."""
    monkeypatch.setattr(tasks_major_news, "_fast_track_major_news_events", lambda *_a, **_k: 0)

    item = _make_candidate(1, "TechCrunch")

    class _FailDB:
        def __init__(self):
            self.rolled_back = False

        def query(self, _model):
            return _FakeQueryChain([item])

        def commit(self):
            raise RuntimeError("DB offline")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

    db = _FailDB()

    result = tasks_major_news._retro_classify_premium_breaking(
        db,
        _make_llm(major=True),
        sources=["TechCrunch"],
        max_items=10,
        lookback_hours=24,
        now=datetime.utcnow(),
    )

    assert result == {"classified": 0, "major": 0}
    assert db.rolled_back


def test_retro_classify_handles_fast_track_commit_failure_gracefully(monkeypatch):
    """Second commit (post-fast-track) failure → rollback + log, still returns classified count."""
    commit_calls = []

    item = _make_candidate(10, "TechCrunch")

    class _PartialFailDB:
        def __init__(self):
            self.rolled_back = False

        def query(self, _model):
            return _FakeQueryChain([item])

        def commit(self):
            commit_calls.append(True)
            if len(commit_calls) == 2:
                raise RuntimeError("second commit failed")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

    db = _PartialFailDB()
    monkeypatch.setattr(
        tasks_major_news,
        "_fast_track_major_news_events",
        lambda _db, *, content_item_ids, now: len(content_item_ids),
    )

    result = tasks_major_news._retro_classify_premium_breaking(
        db,
        _make_llm(major=True),
        sources=["TechCrunch"],
        max_items=10,
        lookback_hours=24,
        now=datetime.utcnow(),
    )

    # Classification was committed (first commit succeeded), result is valid
    assert result["classified"] == 1
    assert result["major"] == 1
    # Second commit failed and was rolled back — no crash
    assert db.rolled_back


def test_run_major_news_probe_job_includes_retro_stats(monkeypatch):
    """run_major_news_probe_job() result dict includes retro_classified and retro_major."""

    class _FakeDB:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class _FakeStateRepo:
        def __init__(self, _db):
            pass

        def get_active_cooldown(self, **_):
            return None

        def record_outcome(self, **_):
            return None

    class _FakeRSSClient:
        def __init__(self, feed_configs):
            pass

        def fetch_feed(self, _url, max_entries, **_kwargs):
            return []

        def get_last_fetch_outcome(self, _url):
            return None

    monkeypatch.setattr(tasks_major_news, "memory_over_soft_limit", lambda: False)
    monkeypatch.setattr(tasks_major_news, "_ingestion_lane_recently_running", lambda: False)
    monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(tasks_major_news, "SourceFetchStateRepository", _FakeStateRepo)
    monkeypatch.setattr(tasks_major_news, "RSSClient", _FakeRSSClient)
    monkeypatch.setattr(tasks_major_news, "get_feeds_by_role", lambda _role: [_major_feed()])
    monkeypatch.setattr(
        tasks_major_news,
        "_retro_classify_premium_breaking",
        lambda *_a, **_k: {"classified": 3, "major": 1},
    )

    result = tasks_major_news.run_major_news_probe_job()

    assert result["retro_classified"] == 3
    assert result["retro_major"] == 1


def test_probe_passes_primary_link_flag_to_fetch_feed(monkeypatch):
    """The probe must forward feed.primary_link_from_description to fetch_feed().

    Regression test for the P1-1b bug: Techmeme source_urls stayed as
    techmeme.com/* because the probe called fetch_feed() without the flag.
    """
    fetch_kwargs_seen: list[dict] = []

    class _FakeDB:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class _FakeStateRepo:
        def __init__(self, _db):
            pass

        def get_active_cooldown(self, **_):
            return None

        def record_outcome(self, **_):
            return None

    class _FakeRSSClient:
        def __init__(self, feed_configs):
            pass

        def fetch_feed(self, _url, max_entries, **kwargs):
            fetch_kwargs_seen.append(kwargs)
            return []

        def get_last_fetch_outcome(self, _url):
            return None

    # Feed with primary_link_from_description=True (e.g. Techmeme)
    techmeme_feed = FeedConfig(
        url="https://www.techmeme.com/feed.xml",
        name="Techmeme",
        role=FeedRole.MAJOR_NEWS,
        quality_tier=QualityTier.PREMIUM,
        daily_cap=5,
        decay_profile=DecayProfile.FAST,
        primary_link_from_description=True,
    )

    monkeypatch.setattr(tasks_major_news, "memory_over_soft_limit", lambda: False)
    monkeypatch.setattr(tasks_major_news, "_ingestion_lane_recently_running", lambda: False)
    monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(tasks_major_news, "SourceFetchStateRepository", _FakeStateRepo)
    monkeypatch.setattr(tasks_major_news, "RSSClient", _FakeRSSClient)
    monkeypatch.setattr(tasks_major_news, "get_feeds_by_role", lambda _role: [techmeme_feed])
    monkeypatch.setattr(
        tasks_major_news,
        "_retro_classify_premium_breaking",
        lambda *_a, **_k: {"classified": 0, "major": 0},
    )

    tasks_major_news.run_major_news_probe_job()

    assert fetch_kwargs_seen, "fetch_feed was never called"
    assert fetch_kwargs_seen[0].get("primary_link_from_description") is True, (
        "probe did not forward primary_link_from_description=True to fetch_feed(); "
        "Techmeme source_urls will remain techmeme.com/* instead of the real article URL"
    )
