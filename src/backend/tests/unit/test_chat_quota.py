"""
Unit tests for chat quota enforcement.

Tests the quota checking and enforcement logic that limits
API usage per device/user.
"""

from unittest.mock import MagicMock

import fakeredis
import pytest
from freezegun import freeze_time

from app.services.quota_manager import QuotaManager

pytestmark = [pytest.mark.unit]

# Pin quota limits so tests don't depend on env overrides
_DAILY_LIMIT = 5
_ARTICLE_LIMIT = 3


class FakeUsageRepo:
    """Test double for usage repository."""

    def __init__(self, daily_usage: int = 0, article_usage: int = 0):
        self.daily_usage = daily_usage
        self.article_usage = article_usage
        self.calls = []

    def get_daily_usage(self, device_id: str) -> int:
        self.calls.append(("daily", device_id))
        return self.daily_usage

    def get_article_usage(self, device_id: str, article_id: int) -> int:
        self.calls.append(("article", device_id, article_id))
        return self.article_usage

    def record_usage(self, device_id: str, article_id, tokens: int):
        self.calls.append(("record", device_id, article_id, tokens))


class TestQuotaEnforcement:
    """Test quota enforcement at different usage levels."""

    def _make_manager(self, daily_usage=0, article_usage=0):
        """Create a QuotaManager with pinned limits (independent of env vars)."""
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        repo = FakeUsageRepo(daily_usage=daily_usage, article_usage=article_usage)
        manager = QuotaManager(repo, redis_client)
        manager.max_per_day = _DAILY_LIMIT
        manager.max_per_article = _ARTICLE_LIMIT
        return manager

    def test_fresh_user_has_full_quota(self):
        """New user with no usage should have full quota."""
        manager = self._make_manager(daily_usage=0)
        quota = manager.check_quota("new-device-123")

        # Should have maximum remaining
        assert quota["remaining_daily_messages"] == _DAILY_LIMIT

    def test_user_near_limit_gets_warning(self):
        """User approaching limit should still have access but limited."""
        manager = self._make_manager(daily_usage=4)
        quota = manager.check_quota("active-device-123")

        assert 0 < quota["remaining_daily_messages"] <= 2

    def test_user_at_limit_denied(self):
        """User who has hit daily limit should be denied."""
        manager = self._make_manager(daily_usage=_DAILY_LIMIT)
        quota = manager.check_quota("heavy-user-device")

        assert quota["remaining_daily_messages"] == 0

    def test_article_quota_enforced(self):
        """Per-article quota should be checked when article_id provided."""
        manager = self._make_manager(daily_usage=0, article_usage=_ARTICLE_LIMIT)
        quota = manager.check_quota("user-device", article_id=123)

        # Article-level quota should impact result
        assert "remaining_article_messages" in quota


class TestQuotaCaching:
    """Test quota caching behavior."""

    @freeze_time("2025-01-01 12:00:00")
    def test_quota_cached_on_check(self):
        """Quota result should be cached in Redis."""
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        repo = FakeUsageRepo(daily_usage=5)
        manager = QuotaManager(repo, redis_client)

        # First check hits DB
        quota1 = manager.check_quota("cache-test-device")

        # Verify cache was written
        cached = redis_client.get("quota:cache-test-device")
        assert cached is not None

        # Second check should use cache (repo not called again)
        quota2 = manager.check_quota("cache-test-device")

        assert quota1 == quota2
        # Only one DB call for daily
        assert len([c for c in repo.calls if c[0] == "daily"]) == 1

    @freeze_time("2025-01-01 12:00:00")
    def test_cache_invalidation_on_record(self):
        """Cache should be invalidated when usage is recorded."""
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        repo = FakeUsageRepo(daily_usage=5)
        manager = QuotaManager(repo, redis_client)

        # First check - caches result
        manager.check_quota("device-invalidate")

        # Verify cache exists
        assert redis_client.exists("quota:device-invalidate")

        # Record usage - should invalidate cache
        manager.update_usage("device-invalidate", article_id=None, tokens=100)

        # Cache should be cleared
        assert not redis_client.exists("quota:device-invalidate")

    @freeze_time("2025-01-01 12:00:00")
    def test_article_quota_uses_dedicated_cache_key(self):
        """Article-specific checks should not reuse the plain device cache entry."""
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        repo = FakeUsageRepo(daily_usage=1, article_usage=2)
        manager = QuotaManager(repo, redis_client)

        manager.check_quota("device-article", article_id=42)

        assert redis_client.exists("quota:device-article:content:42")
        assert not redis_client.exists("quota:device-article")


class TestQuotaEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_device_id_still_works(self):
        """Empty or unusual device IDs should be handled."""
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        repo = FakeUsageRepo()
        manager = QuotaManager(repo, redis_client)

        # Should not raise
        quota = manager.check_quota("")
        assert quota is not None

    def test_redis_unavailable_falls_back(self):
        """Should still work if Redis is unavailable."""
        # Use a mock that raises on all operations
        broken_redis = MagicMock()
        broken_redis.get.side_effect = Exception("Redis down")
        broken_redis.setex.side_effect = Exception("Redis down")

        repo = FakeUsageRepo()
        manager = QuotaManager(repo, broken_redis)

        # Should still return quota result from DB
        quota = manager.check_quota("device-redis-down")
        assert quota is not None
        assert "remaining_daily_messages" in quota

    def test_negative_usage_treated_as_zero(self):
        """Negative usage values should be treated as zero."""
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        repo = FakeUsageRepo(daily_usage=-5)  # Invalid negative
        manager = QuotaManager(repo, redis_client)

        quota = manager.check_quota("device-negative")

        # Should not crash, should have full quota
        assert quota["remaining_daily_messages"] > 0
