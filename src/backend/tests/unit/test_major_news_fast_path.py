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
    monkeypatch.setattr(tasks_major_news, "memory_over_soft_limit", lambda: True)
    monkeypatch.setattr(tasks_major_news, "current_worker_memory_mb", lambda: 1500.0)
    monkeypatch.setattr(tasks_major_news, "memory_soft_limit_mb", lambda: 1400)

    result = tasks_major_news.run_major_news_probe_job()

    assert result["status"] == "skipped_memory"


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

        def fetch_feed(self, _url, max_entries):
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
    monkeypatch.setenv("MAJOR_NEWS_PROBE_MAX_INSERTED", "2")
    monkeypatch.setenv("MAJOR_NEWS_PROBE_ENTRIES_PER_FEED", "5")

    result = tasks_major_news.run_major_news_probe_job()

    assert result["inserted"] == 2
    assert len(inserted_batches[0]) == 2


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

        def fetch_feed(self, _url, max_entries):
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
