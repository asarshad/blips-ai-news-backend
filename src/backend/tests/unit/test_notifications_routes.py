from __future__ import annotations

from app.api.routes import notifications as notifications_module
from app.schemas.push import PushSubscriptionDeleteRequest, PushSubscriptionUpsertRequest


class _FakePushService:
    def __init__(self):
        self.upserts = []
        self.deletes = []

    def upsert_subscription(self, *, device_id: str, token: str, platform: str):
        self.upserts.append((device_id, token, platform))
        return type(
            "Subscription",
            (),
            {
                "active": True,
                "token": token,
                "platform": platform,
            },
        )()

    def delete_subscription(self, *, device_id: str, token: str):
        self.deletes.append((device_id, token))
        return 1


def test_get_device_id_rejects_invalid_header():
    try:
        notifications_module.get_device_id("short")
    except Exception as exc:  # pragma: no cover - explicit assertion below
        assert getattr(exc, "status_code", None) == 400
    else:  # pragma: no cover
        raise AssertionError("Expected HTTPException for short device id")


def test_upsert_push_subscription_passes_device_token_and_platform():
    service = _FakePushService()
    request = PushSubscriptionUpsertRequest(
        token="a" * 32,
        platform="android",
    )

    response = notifications_module.upsert_push_subscription(
        request=request,
        device_id="device-12345678",
        push_service=service,
    )

    assert response.success is True
    assert response.active is True
    assert response.token == "a" * 32
    assert response.platform == "android"
    assert service.upserts == [("device-12345678", "a" * 32, "android")]


def test_delete_push_subscription_returns_inactive_response():
    service = _FakePushService()
    request = PushSubscriptionDeleteRequest(token="b" * 32)

    response = notifications_module.delete_push_subscription(
        request=request,
        device_id="device-abcdefgh",
        push_service=service,
    )

    assert response.success is True
    assert response.active is False
    assert response.token == "b" * 32
    assert response.platform is None
    assert service.deletes == [("device-abcdefgh", "b" * 32)]
