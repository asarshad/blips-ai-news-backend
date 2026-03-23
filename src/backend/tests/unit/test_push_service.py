from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.content import ContentStatus, ContentType
from app.schemas.push import PushMode, PushRuntimeConfig, PushSendResponse
from app.services.push_service import PushNotificationError, PushNotificationService


class _FakeConfigService:
    def __init__(self, config: PushRuntimeConfig):
        self._config = config

    def get_raw_config(self):
        return self._config, "default"


class _FakeMessagingClient:
    def __init__(self, *, is_available: bool, availability_error: str | None = None):
        self.is_available = is_available
        self.availability_error = availability_error

    def send_multicast(self, *, tokens, title, body, data):
        return SimpleNamespace(success_count=len(tokens), failure_count=0, invalid_tokens=[])


class _SequentialFirstQuery:
    def __init__(self, items):
        self._items = items

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        if not self._items:
            return None
        return self._items.pop(0)


class _FakeDBForAuto:
    def __init__(self, items):
        self._items = list(items)

    def query(self, _model):
        return _SequentialFirstQuery(self._items)


def test_send_manual_rejects_when_push_runtime_disabled():
    service = PushNotificationService(
        db=MagicMock(),
        config_service=_FakeConfigService(
            PushRuntimeConfig(enabled=False, mode=PushMode.manual, config_ttl_seconds=300),
        ),
        messaging_client=_FakeMessagingClient(is_available=True),
    )

    try:
        service.send_manual(content_id=1, actor="admin")
    except PushNotificationError as exc:
        assert str(exc) == "Push notifications are disabled"
    else:  # pragma: no cover
        raise AssertionError("Expected PushNotificationError when push is disabled")


def test_send_auto_returns_empty_when_mode_not_auto():
    service = PushNotificationService(
        db=MagicMock(),
        config_service=_FakeConfigService(
            PushRuntimeConfig(enabled=True, mode=PushMode.manual, config_ttl_seconds=300),
        ),
        messaging_client=_FakeMessagingClient(is_available=True),
    )

    result = service.send_auto_for_content_ids([10, 11], actor="scheduler")

    assert result == []


def test_send_auto_deduplicates_ids_and_skips_non_eligible(monkeypatch):
    eligible = SimpleNamespace(
        id=1,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        type=ContentType.ARTICLE,
        title="Eligible",
    )
    candidate = SimpleNamespace(
        id=2,
        curation_status=ContentStatus.CANDIDATE,
        is_suppressed=False,
        type=ContentType.ARTICLE,
        title="Candidate",
    )
    reel = SimpleNamespace(
        id=3,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        type=ContentType.REEL,
        title="Reel",
        duration_seconds=30,
        source_url="https://www.youtube.com/shorts/abc123",
    )
    db = _FakeDBForAuto([eligible, candidate, reel])
    service = PushNotificationService(
        db=db,
        config_service=_FakeConfigService(
            PushRuntimeConfig(enabled=True, mode=PushMode.auto_all, config_ttl_seconds=300),
        ),
        messaging_client=_FakeMessagingClient(is_available=True),
    )
    calls: list[tuple[int, str, str, str | None]] = []

    def _fake_send_item(*, item, mode, actor, auto_dedup_key):
        calls.append((item.id, mode, actor, auto_dedup_key))
        return PushSendResponse(
            success=True,
            skipped=False,
            content_id=item.id,
            mode=mode,
            audience_count=1,
            success_count=1,
            failure_count=0,
            invalid_token_count=0,
            log_id=99,
            message="sent",
        )

    monkeypatch.setattr(service, "_send_item", _fake_send_item)

    results = service.send_auto_for_content_ids([1, 1, 2, 3], actor="scheduler:promotion")

    assert len(results) == 1
    assert results[0].content_id == 1
    assert calls == [(1, "auto_all", "scheduler:promotion", "auto:1")]
