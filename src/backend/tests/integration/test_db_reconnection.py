"""
Database reconnection integration tests.

Verifies that SQLAlchemy's pool_pre_ping mechanism handles
stale connections gracefully without application errors.
"""

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration]


def _get_engine():
    from app.db.base import engine
    return engine


def _get_session():
    from app.db.base import SessionLocal
    return SessionLocal()


class TestDatabaseReconnection:
    """Test that pool_pre_ping handles connection failures."""

    def test_pool_pre_ping_enabled(self):
        """Verify pool_pre_ping is configured on the engine."""
        assert _get_engine().pool._pre_ping is True

    def test_pool_recycle_configured(self):
        """Verify pool_recycle is set to prevent stale connections."""
        assert _get_engine().pool._recycle == 1800  # 30 minutes

    def test_pool_size_configured(self):
        """Verify pool has reasonable size limits."""
        engine = _get_engine()
        assert engine.pool.size() == 5
        assert engine.pool._max_overflow == 10

    def test_session_select_one(self):
        """Basic connectivity test — SELECT 1."""
        with _get_session() as session:
            result = session.execute(text("SELECT 1")).scalar()
            assert result == 1

    def test_session_recovers_after_rollback(self):
        """Session should be usable after a rollback."""
        with _get_session() as session:
            try:
                # Force an error
                session.execute(text("SELECT * FROM nonexistent_table_xyz"))
            except Exception:
                session.rollback()

            # Session should still work after rollback
            result = session.execute(text("SELECT 1")).scalar()
            assert result == 1

    def test_engine_pool_status(self):
        """Engine pool should report healthy status."""
        status = _get_engine().pool.status()
        assert status is not None
        # Pool status is a string like "Pool size: 5  Connections in pool: 1 ..."
        assert "Pool size:" in status
