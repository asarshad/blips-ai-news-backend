import fakeredis
import pytest
from freezegun import freeze_time

from app.services.quota_manager import QuotaManager

pytestmark = [pytest.mark.unit]


def test_quota_manager_caches_quota_with_ttl(monkeypatch):
    redis_client = fakeredis.FakeRedis(decode_responses=True)

    class UsageRepo:
        def __init__(self):
            self.daily_calls = 0
            self.article_calls = 0

        def get_daily_usage(self, device_id: str) -> int:
            self.daily_calls += 1
            return 1

        def get_article_usage(self, device_id: str, article_id: int) -> int:
            self.article_calls += 1
            return 2

        def record_usage(self, device_id: str, article_id, tokens: int):
            raise AssertionError("record_usage should not be called in this test")

    repo = UsageRepo()
    manager = QuotaManager(repo, redis_client)

    with freeze_time("2025-01-01 00:00:00"):
        q1 = manager.check_quota("device-12345678", article_id=99)
        assert q1["remaining_daily_messages"] is not None

        # Cached: second call should not hit DB.
        q2 = manager.check_quota("device-12345678", article_id=99)
        assert q2 == q1

        ttl = redis_client.ttl("quota:device-12345678")
        # fakeredis reports TTL in seconds; allow small drift.
        assert 240 <= ttl <= 300

    assert repo.daily_calls == 1
    assert repo.article_calls == 1
