import os

import pytest
import redis

pytestmark = [pytest.mark.integration]


def test_redis_roundtrip():
    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    client.set("integration:ping", "pong", ex=10)
    assert client.get("integration:ping") == "pong"
