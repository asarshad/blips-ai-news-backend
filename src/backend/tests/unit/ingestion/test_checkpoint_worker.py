from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace

import pytest

from app.ingestion import checkpoint_worker
from app.integrations.youtube_channels import ContentFormat


class _FakeBudgetRepo:
    def __init__(self, _db):  # noqa: D401, ARG002
        pass

    def reserve(self, *, day, content_type, want: int) -> int:  # noqa: ARG002
        return int(want)

    def finalize_batch(self, **_kwargs):
        return


@dataclass
class _Progress:
    id: int
    day_utc: object
    source_type: str
    feed_name: str
    target: int
    items_ingested: int
    items_attempted: int
    status: str
    last_item_cursor: str | None = None
    retry_count: int = 0
    retry_at: datetime | None = None
    last_error: str | None = None


class _FakeQuery:
    def __init__(self, session, result):
        self._session = session
        self._result = result

    def filter(self, *args, **kwargs):  # noqa: ARG002
        return self

    def one(self):
        return self._result

    def update(self, values):
        self._session.updated_values = values
        return 1


class _FakeSession:
    def __init__(self, progress: _Progress):
        self._progress = progress
        self.updated_values = None
        self.committed = False
        self.commit_calls = 0
        self.rollback_calls = 0

    def query(self, _model):  # noqa: ARG002
        return _FakeQuery(self, self._progress)

    def commit(self):
        self.committed = True
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1
        return

    def close(self):
        return


class _FakeRSSClient:
    def __init__(self, entries):
        self.feed_configs = [SimpleNamespace(name="feed1", url="https://example.com/rss")]
        self._entries = entries

    def fetch_feed(self, _url, *, max_entries: int):  # noqa: ARG002
        return list(self._entries)[:max_entries]


class _Entry:
    def __init__(self, url: str):
        self.url = url
        self.title = "t"
        self.content = "c"
        self.image_url = None
        self.published_date = None


class _YTEntry:
    def __init__(self, video_id: str, *, is_short: bool, url: str):
        self.title = "yt"
        self.summary = "summary"
        self.thumbnail_url = "thumb"
        self.source = "YouTube"
        self.category = "Technology"
        self.video_id = video_id
        self.video_url = url
        self.channel_id = "channel-yt"
        self.content_format = ContentFormat.MIXED
        self.is_short = is_short
        self.published_at = datetime.utcnow()
        self.acquisition_lane = "curated"
        self.source_status = "core"
        self.duration_seconds = 42 if is_short else 480
        self.format_fit_score = 1.0


@pytest.fixture(autouse=True)
def _stub_rss_article_metadata(monkeypatch):
    monkeypatch.setattr(
        checkpoint_worker.ArticleHydrationService,
        "fetch_article_page_metadata",
        staticmethod(lambda *_args, **_kwargs: None),
    )


def test_youtube_entry_is_reel_for_134_second_shorts_url():
    entry = SimpleNamespace(
        duration_seconds=134,
        is_short=False,
        video_url="https://www.youtube.com/shorts/VvGaDPViMKY",
    )

    assert checkpoint_worker._youtube_entry_is_reel(entry) is True


def test_youtube_entry_is_not_reel_for_short_watch_url_without_shorts_signal():
    entry = SimpleNamespace(
        duration_seconds=134,
        is_short=False,
        video_url="https://www.youtube.com/watch?v=regular12345",
        title="Foundry IQ: Building the Data Pipeline with Knowledge Sources",
        channel_id="UC9PBzalIcEQCsiIkq36PyUA",
        content_format=None,
    )

    assert checkpoint_worker._youtube_entry_is_reel(entry) is False


def test_build_rss_article_value_returns_none_for_blocked_direct_domain():
    value = checkpoint_worker._build_rss_article_value(
        _Entry("https://github.com/owner/repo"),
        source_url="https://github.com/owner/repo",
        day_utc=datetime.utcnow().date(),
        review_queue_status="PROMOTED",
        article_hydrator=checkpoint_worker.ArticleHydrationService(),
    )

    assert value is None


def test_worker_skips_when_retry_at_in_future(monkeypatch):
    # Inject dummy integration modules to avoid importing feedparser under Python 3.14.
    pkg = ModuleType("app.integrations")
    pkg.__path__ = []  # mark as package
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient([])  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = lambda: None  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)

    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="rss",
        feed_name="feed1",
        target=10,
        items_ingested=0,
        items_attempted=0,
        status="running",
        retry_at=datetime.utcnow() + timedelta(minutes=10),
    )

    monkeypatch.setattr("app.db.base.SessionLocal", lambda: _FakeSession(progress))

    # Stub budgets (should not be hit due to early backoff).
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)

    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=None,
        owner_token="t",
        ttl_ms=1000,
        batch_size=5,
        retry_base_seconds=1,
        retry_max_seconds=10,
    )

    assert result["status"] == "skipped_backoff"
    assert result["attempted"] == 0
    assert result["inserted"] == 0


def test_worker_reports_attempted_when_inserted_zero(monkeypatch):
    # 10 entries; with batch_size=2 and multiplier=3 we should attempt 6 candidates.
    entries = [_Entry(f"https://example.com/{i}") for i in range(10)]
    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="rss",
        feed_name="feed1",
        target=10,
        items_ingested=0,
        items_attempted=0,
        status="running",
        last_item_cursor=None,
    )

    monkeypatch.setenv("INGESTION_CANDIDATE_MULTIPLIER", "3")

    # Inject dummy integration modules to avoid importing feedparser under Python 3.14.
    pkg = ModuleType("app.integrations")
    pkg.__path__ = []  # mark as package
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient(entries)  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = lambda: None  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)

    monkeypatch.setattr("app.db.base.SessionLocal", lambda: _FakeSession(progress))

    # Stub budgets to allow reservation.
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)

    # Avoid Redis and Postgres lock paths.
    monkeypatch.setattr(checkpoint_worker, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker, "release_lease", lambda *_args, **_kwargs: True)

    # Avoid real DB insert; force 0 inserted to validate attempted behavior.
    monkeypatch.setattr(
        checkpoint_worker, "_insert_content_items_postgres", lambda *_args, **_kwargs: 0
    )

    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=object(),
        owner_token="t",
        ttl_ms=1000,
        batch_size=2,
        retry_base_seconds=1,
        retry_max_seconds=10,
    )

    assert result["status"] == "ok"
    assert result["inserted"] == 0
    assert result["attempted"] == 6


def test_youtube_reel_rows_use_uploads_head_scan_when_enabled(monkeypatch):
    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="youtube_reel",
        feed_name="Reel Channel",
        target=1,
        items_ingested=0,
        items_attempted=0,
        status="running",
    )
    session = _FakeSession(progress)
    calls = {"uploads": 0, "rss": 0}

    class _FakeYTClient:
        def __init__(self):
            self.channel_configs = [
                SimpleNamespace(
                    name="Reel Channel",
                    content_format=ContentFormat.MIXED,
                    fresh_published_hours=None,
                    channel_id="channel-yt",
                    effective_daily_reel_cap=10,
                )
            ]

        def fetch_recent_reel_uploads(self, _config, *, max_entries):
            calls["uploads"] += 1
            assert max_entries >= 1
            return (
                [
                    _YTEntry(
                        "short1234567",
                        is_short=True,
                        url="https://www.youtube.com/shorts/short1234567",
                    )
                ],
                {
                    "fetch_strategy": "uploads_api",
                    "playlist_pages_fetched": 1,
                    "video_ids_hydrated": 1,
                    "raw_entries": 1,
                    "classified_reels": 1,
                    "quota_units_requested": 3,
                },
            )

        def _fetch_channel_with_config(self, _config, *, max_videos):
            calls["rss"] += 1
            return []

    pkg = ModuleType("app.integrations")
    pkg.__path__ = []
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient([])  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = _FakeYTClient  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)
    monkeypatch.setattr("app.db.base.SessionLocal", lambda: session)
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)
    monkeypatch.setattr(checkpoint_worker, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker, "release_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker.settings, "YT_REEL_UPLOADS_API_ENABLED", True)
    monkeypatch.setattr(
        checkpoint_worker,
        "_insert_content_items_postgres",
        lambda _db, *, values: len(values),
    )

    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=object(),
        owner_token="t",
        ttl_ms=1000,
        batch_size=1,
        retry_base_seconds=1,
        retry_max_seconds=10,
    )

    assert result["status"] == "ok"
    assert result["inserted"] == 1
    assert calls == {"uploads": 1, "rss": 0}


def test_youtube_video_rows_stay_on_rss_when_reel_uploads_enabled(monkeypatch):
    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="youtube_video",
        feed_name="Video Channel",
        target=1,
        items_ingested=0,
        items_attempted=0,
        status="running",
    )
    session = _FakeSession(progress)
    calls = {"uploads": 0, "rss": 0}

    class _FakeYTClient:
        def __init__(self):
            self.channel_configs = [
                SimpleNamespace(
                    name="Video Channel",
                    content_format=ContentFormat.MIXED,
                    fresh_published_hours=None,
                    channel_id="channel-yt",
                    effective_daily_reel_cap=10,
                )
            ]

        def fetch_recent_reel_uploads(self, _config, *, max_entries):
            calls["uploads"] += 1
            return [], {}

        def _fetch_channel_with_config(self, _config, *, max_videos):
            calls["rss"] += 1
            return [
                _YTEntry(
                    "video1234567",
                    is_short=False,
                    url="https://www.youtube.com/watch?v=video1234567",
                )
            ][:max_videos]

    pkg = ModuleType("app.integrations")
    pkg.__path__ = []
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient([])  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = _FakeYTClient  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)
    monkeypatch.setattr("app.db.base.SessionLocal", lambda: session)
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)
    monkeypatch.setattr(checkpoint_worker, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker, "release_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker.settings, "YT_REEL_UPLOADS_API_ENABLED", True)
    monkeypatch.setattr(
        checkpoint_worker,
        "_insert_content_items_postgres",
        lambda _db, *, values: len(values),
    )

    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=object(),
        owner_token="t",
        ttl_ms=1000,
        batch_size=1,
        retry_base_seconds=1,
        retry_max_seconds=10,
    )

    assert result["status"] == "ok"
    assert result["inserted"] == 1
    assert calls == {"uploads": 0, "rss": 1}


def test_worker_coerces_empty_article_image_to_none(monkeypatch):
    entry = _Entry("https://example.com/with-empty-image")
    entry.image_url = ""
    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="rss",
        feed_name="feed1",
        target=1,
        items_ingested=0,
        items_attempted=0,
        status="running",
        last_item_cursor=None,
    )
    session = _FakeSession(progress)

    pkg = ModuleType("app.integrations")
    pkg.__path__ = []
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient([entry])  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = lambda: None  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)
    monkeypatch.setattr("app.db.base.SessionLocal", lambda: session)
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)
    monkeypatch.setattr(checkpoint_worker, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker, "release_lease", lambda *_args, **_kwargs: True)

    captured_values = []

    def _capture_insert(_db, *, values):
        captured_values.extend(values)
        return len(values)

    monkeypatch.setattr(checkpoint_worker, "_insert_content_items_postgres", _capture_insert)

    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=object(),
        owner_token="t",
        ttl_ms=1000,
        batch_size=1,
        retry_base_seconds=1,
        retry_max_seconds=10,
    )

    assert result["status"] == "ok"
    assert result["inserted"] == 1
    assert captured_values[0]["image_url"] is None


def test_worker_enriches_missing_rss_article_image_from_page_metadata(monkeypatch):
    entry = _Entry("https://example.com/missing-image")
    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="rss",
        feed_name="feed1",
        target=1,
        items_ingested=0,
        items_attempted=0,
        status="running",
        last_item_cursor=None,
    )
    session = _FakeSession(progress)

    pkg = ModuleType("app.integrations")
    pkg.__path__ = []
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient([entry])  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = lambda: None  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)
    monkeypatch.setattr("app.db.base.SessionLocal", lambda: session)
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)
    monkeypatch.setattr(checkpoint_worker, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker, "release_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        checkpoint_worker.ArticleHydrationService,
        "fetch_article_page_metadata",
        staticmethod(
            lambda *_args, **_kwargs: SimpleNamespace(
                title="Recovered title",
                canonical_url="https://example.com/canonical-article",
                image_url="https://cdn.example.com/hero.png",
            )
        ),
    )

    captured_values = []

    def _capture_insert(_db, *, values):
        captured_values.extend(values)
        return len(values)

    monkeypatch.setattr(checkpoint_worker, "_insert_content_items_postgres", _capture_insert)

    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=object(),
        owner_token="t",
        ttl_ms=1000,
        batch_size=1,
        retry_base_seconds=1,
        retry_max_seconds=10,
    )

    assert result["status"] == "ok"
    assert result["inserted"] == 1
    assert captured_values[0]["title"] == "Recovered title"
    assert captured_values[0]["canonical_url"] == "https://example.com/canonical-article"
    assert captured_values[0]["canonical_key"] == checkpoint_worker.canonical_key_for_article(
        canonical_url="https://example.com/canonical-article",
        source_url="https://example.com/missing-image",
    )
    assert captured_values[0]["image_url"] == "https://cdn.example.com/hero.png"


def test_worker_failure_schedules_retry_with_backoff_and_rolls_back(monkeypatch):
    entries = [_Entry("https://example.com/fail")]
    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="rss",
        feed_name="feed1",
        target=10,
        items_ingested=0,
        items_attempted=0,
        status="running",
        retry_count=2,
    )
    session = _FakeSession(progress)

    pkg = ModuleType("app.integrations")
    pkg.__path__ = []
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient(entries)  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = lambda: None  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)
    monkeypatch.setattr("app.db.base.SessionLocal", lambda: session)
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)
    monkeypatch.setattr(checkpoint_worker, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker, "release_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        checkpoint_worker,
        "_insert_content_items_postgres",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("insert boom")),
    )

    before = datetime.utcnow()
    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=object(),
        owner_token="t",
        ttl_ms=1000,
        batch_size=1,
        retry_base_seconds=5,
        retry_max_seconds=60,
    )
    after = datetime.utcnow()

    assert result["status"] == "failed"
    assert "insert boom" in str(result["error"])

    # Insert transaction should rollback, then retry state should be committed.
    assert session.rollback_calls >= 1
    assert session.commit_calls >= 1

    # Retry metadata should be updated with exponential backoff.
    assert progress.status == "running"
    assert progress.last_error == "insert boom"
    assert progress.retry_count == 3
    assert progress.retry_at is not None
    assert before + timedelta(seconds=5) <= progress.retry_at <= after + timedelta(seconds=60)


def test_worker_failure_pauses_degraded_rss_source_and_reallocates_shortfall(monkeypatch):
    entries = [_Entry("https://example.com/fail")]
    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="rss",
        feed_name="feed1",
        target=10,
        items_ingested=2,
        items_attempted=0,
        status="running",
        retry_count=3,
    )
    session = _FakeSession(progress)

    pkg = ModuleType("app.integrations")
    pkg.__path__ = []
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient(entries)  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = lambda: None  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)
    monkeypatch.setattr("app.db.base.SessionLocal", lambda: session)
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)
    monkeypatch.setattr(checkpoint_worker, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker, "release_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        checkpoint_worker,
        "_insert_content_items_postgres",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("insert boom")),
    )

    reallocated = []
    monkeypatch.setattr(
        checkpoint_worker,
        "_reallocate_rss_shortfall",
        lambda *_args, **_kwargs: reallocated.append("called") or {"feed_name": "feed2", "amount": 8},
    )

    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=object(),
        owner_token="t",
        ttl_ms=1000,
        batch_size=1,
        retry_base_seconds=5,
        retry_max_seconds=60,
    )

    assert result["status"] == "failed"
    assert progress.status == "failed"
    assert "Paused for day after repeated RSS failures" in str(progress.last_error)
    assert progress.retry_at is None
    assert reallocated == ["called"]


def test_worker_persists_youtube_metadata_fields(monkeypatch):
    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="youtube_video",
        feed_name="Channel One",
        target=1,
        items_ingested=0,
        items_attempted=0,
        status="running",
    )
    session = _FakeSession(progress)

    class _FakeYouTubeEntry:
        video_id = "abc123DEF45"
        video_url = "https://www.youtube.com/watch?v=abc123DEF45"
        title = "MacBook hands on review"
        summary = "Apple announced a new MacBook and benchmarks look strong."
        source = "Channel One"
        channel_id = "channel-1"
        is_short = False
        published_at = datetime.utcnow()
        acquisition_lane = "curated"
        source_status = "core"
        duration_seconds = 480
        view_count = 120000
        like_count = 3400
        comment_count = 250
        views_per_hour = 5500.0
        format_fit_score = 1.0
        thumbnail_url = "https://img.youtube.com/vi/abc123DEF45/hqdefault.jpg"

    class _FakeYouTubeClient:
        def __init__(self):
            self.channel_configs = [
                SimpleNamespace(
                    name="Channel One",
                    content_format=SimpleNamespace(value="mixed"),
                    enabled=True,
                    effective_daily_reel_cap=10,
                )
            ]

        def _fetch_channel_with_config(self, _config, max_videos):  # noqa: ARG002
            return [_FakeYouTubeEntry()]

    pkg = ModuleType("app.integrations")
    pkg.__path__ = []
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient([])  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = _FakeYouTubeClient  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)
    monkeypatch.setattr("app.db.base.SessionLocal", lambda: session)
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)
    monkeypatch.setattr(checkpoint_worker, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker, "release_lease", lambda *_args, **_kwargs: True)

    captured_values = []

    def _capture_insert(_db, *, values):
        captured_values.extend(values)
        return len(values)

    monkeypatch.setattr(checkpoint_worker, "_insert_content_items_postgres", _capture_insert)

    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=object(),
        owner_token="t",
        ttl_ms=1000,
        batch_size=1,
        retry_base_seconds=1,
        retry_max_seconds=10,
    )

    assert result["status"] == "ok"
    assert result["inserted"] == 1
    assert len(captured_values) == 1
    inserted = captured_values[0]
    assert inserted["channel_id"] == "channel-1"
    assert inserted["acquisition_lane"] == "curated"
    assert inserted["source_status"] == "core"
    assert inserted["view_count_snapshot"] == 120000
    assert inserted["engagement_snapshot"] == {"likes": 3400, "comments": 250}
    assert inserted["views_per_hour"] == 5500.0
    assert inserted["format_fit_score"] == 1.0
    assert inserted["duration_seconds"] == 480
    assert inserted["discovered_via"] == "yt_curated"


def test_worker_keeps_short_duration_long_form_entry_off_reels(monkeypatch):
    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="youtube_reel",
        feed_name="Channel One",
        target=1,
        items_ingested=0,
        items_attempted=0,
        status="running",
    )
    session = _FakeSession(progress)

    class _FakeYouTubeEntry:
        video_id = "short123DEF4"
        video_url = "https://www.youtube.com/watch?v=short123DEF4"
        title = "Quick AI demo"
        summary = "A short demo from a long-form channel."
        source = "Channel One"
        channel_id = "channel-1"
        is_short = False
        published_at = datetime.utcnow()
        acquisition_lane = "curated"
        source_status = "core"
        duration_seconds = 90
        view_count = 45000
        like_count = 900
        comment_count = 40
        views_per_hour = 1200.0
        format_fit_score = 1.0
        thumbnail_url = "https://img.youtube.com/vi/short123DEF4/hqdefault.jpg"
        default_language = "en"

    class _FakeYouTubeClient:
        def __init__(self):
            self.channel_configs = [
                SimpleNamespace(
                    name="Channel One",
                    content_format=SimpleNamespace(value="long_form"),
                    enabled=True,
                    effective_daily_reel_cap=10,
                )
            ]

        def _fetch_channel_with_config(self, _config, max_videos):  # noqa: ARG002
            return [_FakeYouTubeEntry()]

    pkg = ModuleType("app.integrations")
    pkg.__path__ = []
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient([])  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = _FakeYouTubeClient  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)
    monkeypatch.setattr("app.db.base.SessionLocal", lambda: session)
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)
    monkeypatch.setattr(checkpoint_worker, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker, "release_lease", lambda *_args, **_kwargs: True)

    captured_values = []

    def _capture_insert(_db, *, values):
        captured_values.extend(values)
        return len(values)

    monkeypatch.setattr(checkpoint_worker, "_insert_content_items_postgres", _capture_insert)

    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=object(),
        owner_token="t",
        ttl_ms=1000,
        batch_size=1,
        retry_base_seconds=1,
        retry_max_seconds=10,
    )

    assert result["status"] == "ok"
    assert result["inserted"] == 0
    assert len(captured_values) == 0


def test_worker_uses_channel_specific_lookback_for_long_form_sources(monkeypatch):
    progress = _Progress(
        id=1,
        day_utc=None,
        source_type="youtube_video",
        feed_name="Podcast Channel",
        target=1,
        items_ingested=0,
        items_attempted=0,
        status="running",
    )
    session = _FakeSession(progress)

    class _OldEntry:
        video_id = "podcastold123"
        video_url = "https://www.youtube.com/watch?v=podcastold123"
        title = "Five day old episode"
        summary = "Still inside the global lookback, outside the channel override."
        source = "Podcast Channel"
        channel_id = "podcast-1"
        is_short = False
        published_at = datetime.utcnow() - timedelta(days=5)
        acquisition_lane = "curated"
        source_status = "core"
        duration_seconds = 3600
        view_count = 1000
        like_count = 10
        comment_count = 1
        views_per_hour = 10.0
        format_fit_score = 0.7
        thumbnail_url = "https://img.youtube.com/vi/podcastold123/hqdefault.jpg"
        default_language = "en"

    class _FakeYouTubeClient:
        def __init__(self):
            self.channel_configs = [
                SimpleNamespace(
                    name="Podcast Channel",
                    content_format=SimpleNamespace(value="long_form"),
                    enabled=True,
                    fresh_published_hours=72,
                )
            ]

        def _fetch_channel_with_config(self, _config, max_videos):  # noqa: ARG002
            return [_OldEntry()]

    pkg = ModuleType("app.integrations")
    pkg.__path__ = []
    rss_mod = ModuleType("app.integrations.rss_client")
    rss_mod.RSSClient = lambda: _FakeRSSClient([])  # type: ignore[attr-defined]
    yt_mod = ModuleType("app.integrations.youtube_client")
    yt_mod.YouTubeClient = _FakeYouTubeClient  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.integrations", pkg)
    monkeypatch.setitem(sys.modules, "app.integrations.rss_client", rss_mod)
    monkeypatch.setitem(sys.modules, "app.integrations.youtube_client", yt_mod)
    monkeypatch.setattr("app.db.base.SessionLocal", lambda: session)
    monkeypatch.setattr(checkpoint_worker, "IngestionBudgetRepository", _FakeBudgetRepo)
    monkeypatch.setattr(checkpoint_worker, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpoint_worker, "release_lease", lambda *_args, **_kwargs: True)

    captured_values = []

    def _capture_insert(_db, *, values):
        captured_values.extend(values)
        return len(values)

    monkeypatch.setattr(checkpoint_worker, "_insert_content_items_postgres", _capture_insert)

    result = checkpoint_worker.process_progress_row_batch(
        row_id=1,
        day_utc=datetime.utcnow().date(),
        redis_client=object(),
        owner_token="t",
        ttl_ms=1000,
        batch_size=1,
        retry_base_seconds=1,
        retry_max_seconds=10,
    )

    assert result["status"] == "ok"
    assert result["inserted"] == 0
    assert captured_values == []
