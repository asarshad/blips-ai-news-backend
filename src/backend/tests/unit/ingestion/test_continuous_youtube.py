"""Tests for Step 2: Continuous YouTube ingestion (no daily stop conditions).

Covers:
- reopen_youtube_rows: bumps target, resets exhaustion state, skip-locks
- build_defaults: YouTube rows always created (no _should_fill_surface gating)
- Video/reel budget uses high multiplier
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.ingestion_progress import IngestionProgress
from app.repositories.ingestion_progress_repo import IngestionProgressRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeQuery:
    """Minimal query mock that supports filter/with_for_update/one_or_none."""

    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_args, **_kwargs):
        return self

    def with_for_update(self, **_kwargs):
        return self

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


def _make_progress(
    *,
    row_id=1,
    day_utc=date(2026, 6, 1),
    source_type="youtube_video",
    feed_name="TestChannel",
    target=5,
    items_ingested=5,
    items_attempted=20,
    status="complete",
    retry_count=0,
    retry_at=None,
    last_error=None,
    last_item_cursor="abc123",
):
    row = IngestionProgress()
    row.id = row_id
    row.day_utc = day_utc
    row.source_type = source_type
    row.feed_name = feed_name
    row.target = target
    row.items_ingested = items_ingested
    row.items_attempted = items_attempted
    row.status = status
    row.retry_count = retry_count
    row.retry_at = retry_at
    row.last_error = last_error
    row.last_item_cursor = last_item_cursor
    return row


# ---------------------------------------------------------------------------
# reopen_youtube_rows tests
# ---------------------------------------------------------------------------


class TestReopenYoutubeRows:
    """Tests for IngestionProgressRepository.reopen_youtube_rows."""

    def test_reopens_complete_youtube_row(self):
        """A complete YouTube row gets its target bumped and state reset."""
        row = _make_progress(
            source_type="youtube_video",
            target=5,
            items_ingested=5,
            items_attempted=20,
            status="complete",
            retry_count=2,
            retry_at=datetime(2026, 6, 1, 12, 0),
            last_error="some error",
            last_item_cursor="cursor_abc",
        )

        db = MagicMock()
        db.query.return_value = _FakeQuery([row])
        repo = IngestionProgressRepository(db)

        reopened = repo.reopen_youtube_rows(
            day_utc=date(2026, 6, 1),
            defaults=[("youtube_video", "TestChannel", 5)],
        )

        assert reopened == 1
        # Target bumped: ingested (5) + per_cycle (5) = 10
        assert row.target == 10
        assert row.status == "running"
        assert row.items_attempted == 0
        assert row.retry_count == 0
        assert row.retry_at is None
        assert row.last_error is None
        # Cursor preserved
        assert row.last_item_cursor == "cursor_abc"
        db.commit.assert_called_once()

    def test_skips_rss_rows(self):
        """RSS rows are never reopened."""
        db = MagicMock()
        repo = IngestionProgressRepository(db)

        reopened = repo.reopen_youtube_rows(
            day_utc=date(2026, 6, 1),
            defaults=[("rss", "TechCrunch", 10)],
        )

        assert reopened == 0
        db.query.assert_not_called()


class TestReopenContinuousRows:
    def test_reopens_complete_rss_row(self):
        row = _make_progress(
            source_type="rss",
            target=10,
            items_ingested=10,
            items_attempted=25,
            status="complete",
            retry_count=1,
            retry_at=datetime(2026, 6, 1, 12, 0),
            last_error="some error",
        )

        db = MagicMock()
        db.query.return_value = _FakeQuery([row])
        repo = IngestionProgressRepository(db)

        reopened = repo.reopen_continuous_rows(
            day_utc=date(2026, 6, 1),
            defaults=[("rss", "TechCrunch", 10)],
        )

        assert reopened == 1
        assert row.target == 20
        assert row.status == "running"
        assert row.items_attempted == 0
        assert row.retry_count == 0
        assert row.retry_at is None
        assert row.last_error is None
        db.commit.assert_called_once()

    def test_skips_still_running_row(self):
        """A YouTube row that hasn't reached its target is left alone."""
        row = _make_progress(
            source_type="youtube_reel",
            target=5,
            items_ingested=3,
            status="running",
        )

        db = MagicMock()
        db.query.return_value = _FakeQuery([row])
        repo = IngestionProgressRepository(db)

        reopened = repo.reopen_youtube_rows(
            day_utc=date(2026, 6, 1),
            defaults=[("youtube_reel", "TestChannel", 5)],
        )

        assert reopened == 0
        # Target unchanged
        assert row.target == 5

    def test_reopens_row_at_target_but_not_complete_status(self):
        """A row with items_ingested >= target but status='running' is reopened."""
        row = _make_progress(
            source_type="youtube_video",
            target=5,
            items_ingested=6,
            status="running",
        )

        db = MagicMock()
        db.query.return_value = _FakeQuery([row])
        repo = IngestionProgressRepository(db)

        reopened = repo.reopen_youtube_rows(
            day_utc=date(2026, 6, 1),
            defaults=[("youtube_video", "TestChannel", 5)],
        )

        assert reopened == 1
        assert row.target == 11  # 6 + 5

    def test_reopens_failed_exhausted_row_below_target(self):
        """A row exhausted by attempt cap is reopened for another poll cycle."""
        row = _make_progress(
            source_type="youtube_video",
            target=5,
            items_ingested=2,
            items_attempted=150,
            status="failed",
            retry_count=4,
            retry_at=datetime(2026, 6, 1, 12, 0),
            last_error="Exhausted attempts: attempted=150 max=150",
        )

        db = MagicMock()
        db.query.return_value = _FakeQuery([row])
        repo = IngestionProgressRepository(db)

        reopened = repo.reopen_youtube_rows(
            day_utc=date(2026, 6, 1),
            defaults=[("youtube_video", "TestChannel", 5)],
        )

        assert reopened == 1
        assert row.target == 7
        assert row.status == "running"
        assert row.items_attempted == 0
        assert row.retry_count == 0
        assert row.retry_at is None
        assert row.last_error is None

    def test_skips_missing_row(self):
        """If the row doesn't exist yet, skip it."""
        db = MagicMock()
        db.query.return_value = _FakeQuery([])
        repo = IngestionProgressRepository(db)

        reopened = repo.reopen_youtube_rows(
            day_utc=date(2026, 6, 1),
            defaults=[("youtube_video", "NewChannel", 5)],
        )

        assert reopened == 0

    def test_multiple_rows_mixed_types(self):
        """Multiple YouTube rows reopened, RSS skipped."""
        video_row = _make_progress(
            row_id=1,
            source_type="youtube_video",
            feed_name="VideoChannel",
            target=5,
            items_ingested=5,
            status="complete",
        )
        reel_row = _make_progress(
            row_id=2,
            source_type="youtube_reel",
            feed_name="ReelChannel",
            target=3,
            items_ingested=3,
            status="complete",
        )

        call_count = 0

        def fake_query(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _FakeQuery([video_row])
            return _FakeQuery([reel_row])

        db = MagicMock()
        db.query.side_effect = fake_query
        repo = IngestionProgressRepository(db)

        reopened = repo.reopen_youtube_rows(
            day_utc=date(2026, 6, 1),
            defaults=[
                ("rss", "TechCrunch", 10),
                ("youtube_video", "VideoChannel", 5),
                ("youtube_reel", "ReelChannel", 3),
            ],
        )

        assert reopened == 2
        assert video_row.target == 10
        assert reel_row.target == 6


class TestPrimeYoutubeRows:
    def test_prime_youtube_row_resets_cursor_and_raises_target(self):
        row = _make_progress(
            source_type="youtube_video",
            target=1,
            items_ingested=0,
            items_attempted=9,
            status="failed",
            retry_count=3,
            retry_at=datetime(2026, 6, 1, 12, 0),
            last_error="Temporary failure",
            last_item_cursor="cursor_abc",
        )

        db = MagicMock()
        db.query.return_value = _FakeQuery([row])
        repo = IngestionProgressRepository(db)

        primed = repo.prime_youtube_rows(
            day_utc=date(2026, 6, 1),
            rows=[("youtube_video", "TestChannel", 3)],
        )

        assert primed == [1]
        assert row.target == 3
        assert row.status == "running"
        assert row.items_attempted == 0
        assert row.retry_count == 0
        assert row.retry_at is None
        assert row.last_error is None
        assert row.last_item_cursor is None
        db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Budget multiplier test
# ---------------------------------------------------------------------------


def test_checkpointed_ingestion_uses_high_budget_for_youtube(monkeypatch):
    """Video/reel budget targets are 10x to avoid daily caps."""
    from app.ingestion import checkpointing

    monkeypatch.setenv("INGESTION_ENABLED", "true")
    monkeypatch.setenv("INGESTION_CRON_DISABLED", "false")

    fake_day = date(2026, 6, 1)
    monkeypatch.setattr(checkpointing, "get_ingestion_day", lambda: fake_day)

    # Track budget calls
    budget_calls = {}

    class FakeBudgetRepo:
        def __init__(self, _db):
            pass

        def ensure(self, *, day, content_type, target):
            budget_calls[content_type.value] = target

    class FakeProgressRepo:
        def __init__(self, _db):
            self.rows = [
                SimpleNamespace(source_type="rss", feed_name="feed1", target=10),
                SimpleNamespace(source_type="youtube_video", feed_name="ch1", target=5),
                SimpleNamespace(source_type="youtube_reel", feed_name="ch2", target=3),
            ]

        def ensure_rows(self, **_kw):
            return 0

        def reopen_continuous_rows(self, **_kw):
            return 0

        def list_for_day(self, **_kw):
            return self.rows

        def prime_youtube_rows(self, **_kw):
            return []

        def list_incomplete(self, **_kw):
            return []

    class FakeContentRepo:
        def __init__(self, _db):
            pass

        def get_article_supply_counts_on_ingestion_day(self, _day):
            return {"promoted": 100, "ready": 100}

    monkeypatch.setattr(checkpointing, "IngestionBudgetRepository", FakeBudgetRepo)
    monkeypatch.setattr(checkpointing, "IngestionProgressRepository", FakeProgressRepo)
    monkeypatch.setattr(checkpointing, "ContentItemRepository", FakeContentRepo)
    monkeypatch.setattr(checkpointing, "_prime_bootstrap_youtube_rows", lambda **_kw: [])

    # Provide minimal defaults: 1 rss, 1 video, 1 reel channel
    from app.ingestion.checkpoint_defaults import FeedDefault

    monkeypatch.setattr(
        checkpointing,
        "_build_defaults",
        lambda **_kw: [
            FeedDefault("rss", "feed1", 10),
            FeedDefault("youtube_video", "ch1", 5),
            FeedDefault("youtube_reel", "ch2", 3),
        ],
    )

    checkpointing.run_checkpointed_ingestion(
        db=MagicMock(),
        redis_client=None,
    )

    # Article budget is 1x
    assert budget_calls["ARTICLE"] == 10
    # Video/reel budgets are 10x
    assert budget_calls["VIDEO"] == 50
    assert budget_calls["REEL"] == 30


def test_checkpointed_ingestion_expands_article_budget_when_ready_supply_is_below_target(
    monkeypatch,
):
    """Article budget should follow reopened RSS row targets, not just base inserts."""
    from app.ingestion import checkpointing
    from app.ingestion.checkpoint_defaults import FeedDefault
    from app.models.content import ContentType

    monkeypatch.setenv("INGESTION_ENABLED", "true")
    monkeypatch.setenv("INGESTION_CRON_DISABLED", "false")

    fake_day = date(2026, 6, 1)
    monkeypatch.setattr(checkpointing, "get_ingestion_day", lambda: fake_day)

    budget_calls = {}

    class _Row:
        def __init__(self, source_type, feed_name, target):
            self.source_type = source_type
            self.feed_name = feed_name
            self.target = target

    class FakeBudgetRepo:
        def __init__(self, _db):
            pass

        def ensure(self, *, day, content_type, target):
            budget_calls[content_type.value] = target

    class FakeProgressRepo:
        def __init__(self, _db):
            self.rows = [
                _Row("rss", "feed1", 10),
                _Row("youtube_video", "ch1", 5),
                _Row("youtube_reel", "ch2", 3),
            ]

        def ensure_rows(self, **_kw):
            return 0

        def reopen_continuous_rows(self, *, defaults, **_kw):
            reopened = 0
            for source_type, feed_name, per_cycle_target in defaults:
                for row in self.rows:
                    if row.source_type == source_type and row.feed_name == feed_name:
                        row.target += per_cycle_target
                        reopened += 1
            return reopened

        def list_for_day(self, **_kw):
            return self.rows

        def prime_youtube_rows(self, **_kw):
            return []

        def list_incomplete(self, **_kw):
            return []

    class FakeContentRepo:
        def __init__(self, _db):
            pass

        def get_article_supply_counts_on_ingestion_day(self, _day):
            return {"promoted": 32, "ready": 18}

    monkeypatch.setattr(checkpointing, "IngestionBudgetRepository", FakeBudgetRepo)
    monkeypatch.setattr(checkpointing, "IngestionProgressRepository", FakeProgressRepo)
    monkeypatch.setattr(checkpointing, "ContentItemRepository", FakeContentRepo)
    monkeypatch.setattr(checkpointing, "_prime_bootstrap_youtube_rows", lambda **_kw: [])
    monkeypatch.setattr(
        checkpointing,
        "_build_defaults",
        lambda **_kw: [
            FeedDefault("rss", "feed1", 10),
            FeedDefault("youtube_video", "ch1", 5),
            FeedDefault("youtube_reel", "ch2", 3),
        ],
    )

    checkpointing.run_checkpointed_ingestion(db=MagicMock(), redis_client=None)

    assert budget_calls[ContentType.ARTICLE.value] == 20
    assert budget_calls[ContentType.VIDEO.value] == 50
    assert budget_calls[ContentType.REEL.value] == 30
