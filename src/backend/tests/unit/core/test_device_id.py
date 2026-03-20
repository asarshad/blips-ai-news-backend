import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.device_id import resolve_device_id, validate_device_id

pytestmark = [pytest.mark.unit]


def _request(*, user_agent: str = "test-agent", client_ip: str = "203.0.113.10") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"user-agent", user_agent.encode("utf-8"))],
        "client": (client_ip, 12345),
    }
    return Request(scope)


def test_validate_device_id_accepts_uuid_like_values():
    assert validate_device_id("550e8400-e29b-41d4-a716-446655440000") == (
        "550e8400-e29b-41d4-a716-446655440000"
    )


def test_resolve_device_id_prefers_explicit_header():
    request = _request()
    assert (
        resolve_device_id(
            request,
            x_device_id="550e8400-e29b-41d4-a716-446655440000",
        )
        == "550e8400-e29b-41d4-a716-446655440000"
    )


def test_resolve_device_id_rejects_invalid_explicit_header():
    request = _request()
    with pytest.raises(HTTPException) as excinfo:
        resolve_device_id(request, x_device_id="bad id with spaces")

    assert excinfo.value.status_code == 400


def test_resolve_device_id_falls_back_to_hashed_identifier_when_header_missing():
    request = _request()
    fallback = resolve_device_id(request, user_agent="test-agent")

    assert len(fallback) == 32
    assert fallback != "203.0.113.10"
