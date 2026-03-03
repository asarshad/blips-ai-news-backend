"""Unit tests for data retention cleanup service.

Tests:
- Retention selection logic with frozen timestamps
- Protected items (editorial_boost > 0, manual_added=True) are NOT deleted
- Each table is cleaned according to its configured retention period
- Result dataclass is populated correctly
"""

from datetime import datetime, timedelta
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal fakes — we avoid importing the full SQLAlchemy model graph which
# pulls in pydantic-settings / FastAPI.  Instead we exercise the retention
# module's public surface by mocking the DB session.
# ---------------------------------------------------------------------------


class FakeResult:
    """Mimics SQLAlchemy CursorResult with a rowcount."""

    def __init__(self, rowcount: int = 0):
        self.rowcount = rowcount


class FakeSession:
    """Minimal Session double supporting execute/commit/rollback."""

    def __init__(self, results: Optional[dict] = None):
        self._results = results or {}
        self._default_count = 0
        self.committed = False
        self.rolled_back = False

    def execute(self, stmt, params=None):
        # Return different rowcount per table based on the SQL text
        sql_text = str(stmt)
        for table, count in self._results.items():
            if table in sql_text:
                return FakeResult(count)
        return FakeResult(self._default_count)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


# ---------------------------------------------------------------------------
# Freeze the settings that the retention module reads
# ---------------------------------------------------------------------------

# Force submodule registration before any test runs — prevents
# AttributeError when app.services is already cached by another test module.
import app.services.retention_service as _retention_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch):
    """Provide deterministic settings without requiring a real .env file."""
    fake_settings = MagicMock()
    fake_settings.RETAIN_CONTENT_DAYS = 90
    fake_settings.RETAIN_INGESTION_PROGRESS_DAYS = 14
    fake_settings.RETAIN_EVENTS_DAYS = 30
    fake_settings.RETAIN_CONVERSATIONS_DAYS = 30
    fake_settings.RETAIN_USAGE_DAYS = 90
    fake_settings.RETAIN_EDITORIAL_DAYS = 180
    fake_settings.RETAIN_DEBUG_DAYS = 7

    monkeypatch.setattr(_retention_mod, "settings", fake_settings)
    return fake_settings


# ===================================================================
# Tests
# ===================================================================


class TestCleanupResult:
    """Tests for the CleanupResult dataclass."""

    def test_total_deleted_sums_all_tables(self):
        from app.services.retention_service import CleanupResult

        r = CleanupResult()
        r.content_items_deleted = 10
        r.ingestion_progress_deleted = 5
        r.ingestion_budgets_deleted = 3
        r.source_daily_stats_deleted = 2
        r.editorial_actions_deleted = 1
        r.interaction_events_deleted = 20
        r.conversations_deleted = 8
        r.usage_deleted = 15
        assert r.total_deleted == 64

    def test_to_dict_has_all_keys(self):
        from app.services.retention_service import CleanupResult

        r = CleanupResult()
        r.complete()
        d = r.to_dict()
        assert "total_deleted" in d
        assert "tables" in d
        assert "duration_seconds" in d
        assert set(d["tables"].keys()) == {
            "content_items",
            "ingestion_progress",
            "ingestion_budgets",
            "source_daily_stats",
            "editorial_actions",
            "interaction_events",
            "conversations",
            "usage",
        }

    def test_duration_seconds(self):
        from app.services.retention_service import CleanupResult

        r = CleanupResult(started_at=datetime(2025, 1, 1, 0, 0, 0))
        r.ended_at = datetime(2025, 1, 1, 0, 0, 10)
        assert r.duration_seconds == 10.0


class TestRetentionLogic:
    """Verify run_retention_cleanup dispatches correctly and aggregates results."""

    def test_all_tables_cleaned(self):
        """Each table handler gets called and results are aggregated."""
        from app.services.retention_service import run_retention_cleanup

        db = FakeSession(
            results={
                "content_items": 12,
                "ingestion_progress": 5,
                "ingestion_budgets": 3,
                "source_daily_stats": 2,
                "editorial_actions": 1,
                "interaction_events": 20,
                "conversations": 8,
                "usage": 15,
            }
        )
        result = run_retention_cleanup(db)

        assert result.content_items_deleted == 12
        assert result.ingestion_progress_deleted == 5
        assert result.ingestion_budgets_deleted == 3
        assert result.source_daily_stats_deleted == 2
        assert result.editorial_actions_deleted == 1
        assert result.interaction_events_deleted == 20
        assert result.conversations_deleted == 8
        assert result.usage_deleted == 15
        assert result.total_deleted == 66
        assert result.ended_at is not None
        assert not result.errors

    def test_empty_database_returns_zeros(self):
        from app.services.retention_service import run_retention_cleanup

        db = FakeSession()  # all rowcounts default to 0
        result = run_retention_cleanup(db)

        assert result.total_deleted == 0
        assert not result.errors

    def test_error_in_one_table_does_not_block_others(self):
        """If one table's cleanup raises, others still proceed."""
        from app.services.retention_service import run_retention_cleanup

        call_count = 0
        original_execute = FakeSession.execute

        def flaky_execute(self, stmt, params=None):
            nonlocal call_count
            call_count += 1
            sql_text = str(stmt)
            # Fail only for content_items (the first cleanup)
            if "content_items" in sql_text:
                raise RuntimeError("simulated PG lock timeout")
            return original_execute(self, stmt, params)

        db = FakeSession(
            results={
                "ingestion_progress": 3,
                "interaction_events": 7,
            }
        )
        db.execute = lambda stmt, params=None: flaky_execute(db, stmt, params)

        result = run_retention_cleanup(db)

        # content_items should have errored
        assert result.content_items_deleted == 0
        assert any("content_items" in e for e in result.errors)

        # Other tables should still have been cleaned
        assert result.ingestion_progress_deleted == 3
        assert result.interaction_events_deleted == 7


class TestContentItemProtection:
    """Verify that editorial/manual items are protected from deletion."""

    def test_sql_excludes_editorial_and_manual(self):
        """The DELETE statement must include protection clauses."""
        from app.services.retention_service import CleanupResult, _cleanup_content_items

        captured_sql = []

        class CapturingSession:
            def execute(self, stmt, params=None):
                captured_sql.append(str(stmt))
                return FakeResult(0)

            def commit(self):
                pass

            def rollback(self):
                pass

        db = CapturingSession()
        result = CleanupResult()
        _cleanup_content_items(db, datetime.utcnow(), result)

        assert len(captured_sql) == 1
        sql = captured_sql[0].lower()
        # Must exclude editorial_boost > 0
        assert "editorial_boost" in sql
        # Must exclude manual_added = true
        assert "manual_added" in sql
        # Must have a LIMIT to bound batch size
        assert "limit" in sql

    def test_cutoff_uses_retain_content_days(self, _mock_settings):
        """Cutoff should be now - RETAIN_CONTENT_DAYS."""
        from app.services.retention_service import CleanupResult, _cleanup_content_items

        _mock_settings.RETAIN_CONTENT_DAYS = 60

        captured_params = []

        class CapturingSession:
            def execute(self, stmt, params=None):
                captured_params.append(params)
                return FakeResult(0)

            def commit(self):
                pass

            def rollback(self):
                pass

        now = datetime(2025, 6, 1, 12, 0, 0)
        db = CapturingSession()
        result = CleanupResult()
        _cleanup_content_items(db, now, result)

        assert len(captured_params) == 1
        cutoff = captured_params[0]["cutoff"]
        expected = now - timedelta(days=60)
        assert cutoff == expected


class TestGenericTableCleanup:
    """Tests for _cleanup_table helper."""

    def test_date_column_uses_date_cutoff(self, _mock_settings):
        from app.services.retention_service import CleanupResult, _cleanup_table

        captured_params = []

        class CapturingSession:
            def execute(self, stmt, params=None):
                captured_params.append(params)
                return FakeResult(5)

            def commit(self):
                pass

            def rollback(self):
                pass

        now = datetime(2025, 3, 1)
        result = CleanupResult()
        _cleanup_table(
            CapturingSession(),
            now,
            result,
            table="ingestion_budgets",
            column="day",
            days=14,
            attr="ingestion_budgets_deleted",
            is_date=True,
        )

        assert result.ingestion_budgets_deleted == 5
        cutoff = captured_params[0]["cutoff"]
        # Should be a date, not datetime
        from datetime import date

        assert isinstance(cutoff, date)
        assert not isinstance(cutoff, datetime)

    def test_datetime_column_uses_datetime_cutoff(self, _mock_settings):
        from app.services.retention_service import CleanupResult, _cleanup_table

        captured_params = []

        class CapturingSession:
            def execute(self, stmt, params=None):
                captured_params.append(params)
                return FakeResult(3)

            def commit(self):
                pass

            def rollback(self):
                pass

        now = datetime(2025, 3, 1, 12, 0, 0)
        result = CleanupResult()
        _cleanup_table(
            CapturingSession(),
            now,
            result,
            table="interaction_events",
            column="created_at",
            days=30,
            attr="interaction_events_deleted",
        )

        assert result.interaction_events_deleted == 3
        cutoff = captured_params[0]["cutoff"]
        assert isinstance(cutoff, datetime)

    def test_rollback_on_error(self, _mock_settings):
        from app.services.retention_service import CleanupResult, _cleanup_table

        class ErrorSession:
            rolled_back = False

            def execute(self, stmt, params=None):
                raise RuntimeError("connection reset")

            def commit(self):
                pass

            def rollback(self):
                self.rolled_back = True

        db = ErrorSession()
        result = CleanupResult()
        _cleanup_table(
            db,
            datetime.utcnow(),
            result,
            table="usage",
            column="timestamp",
            days=90,
            attr="usage_deleted",
        )

        assert result.usage_deleted == 0
        assert db.rolled_back
        assert any("usage" in e for e in result.errors)


class TestCleanupSchedulerLock:
    """Verify the Redis lock prevents parallel cleanup runs."""

    def test_lock_prevents_duplicate_run(self):
        """When lock is held, cleanup returns skipped."""
        from app.scheduler.tasks_cleanup import run_data_cleanup_job

        with patch("app.scheduler.tasks_cleanup._acquire_cleanup_lock", return_value=False):
            result = run_data_cleanup_job()

        assert result.get("skipped") is True
        assert "lock_held" in result.get("reason", "")

    def test_lock_acquired_runs_cleanup(self):
        """When lock is free, cleanup proceeds."""
        from app.scheduler.tasks_cleanup import run_data_cleanup_job

        mock_db = FakeSession()
        mock_db.close = lambda: None  # FakeSession needs close() for finally block

        with (
            patch("app.scheduler.tasks_cleanup._acquire_cleanup_lock", return_value=True),
            patch("app.scheduler.tasks_cleanup._release_cleanup_lock"),
            patch("app.scheduler.tasks_cleanup.SessionLocal", return_value=mock_db),
        ):
            result = run_data_cleanup_job()

        assert "total_deleted" in result
        assert result["total_deleted"] == 0  # empty DB
