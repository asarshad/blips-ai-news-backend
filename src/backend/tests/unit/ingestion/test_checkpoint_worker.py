from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace

from app.ingestion import checkpointing


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

    def query(self, _model):  # noqa: ARG002
        return _FakeQuery(self, self._progress)

    def commit(self):
        self.committed = True

    def rollback(self):
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
    monkeypatch.setattr(checkpointing, "IngestionBudgetRepository", _FakeBudgetRepo)

    result = checkpointing._process_progress_row_batch(
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
    monkeypatch.setattr(checkpointing, "IngestionBudgetRepository", _FakeBudgetRepo)

    # Avoid Redis and Postgres lock paths.
    monkeypatch.setattr(checkpointing, "claim_lease", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(checkpointing, "release_lease", lambda *_args, **_kwargs: True)

    # Avoid real DB insert; force 0 inserted to validate attempted behavior.
    monkeypatch.setattr(checkpointing, "_insert_content_items_postgres", lambda *_args, **_kwargs: 0)

    result = checkpointing._process_progress_row_batch(
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
