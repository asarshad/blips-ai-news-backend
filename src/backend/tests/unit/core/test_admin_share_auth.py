import pytest
from fastapi import HTTPException

from app.core.auth import is_valid_admin_share_token, require_admin_share_token
from app.core.config import settings

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def restore_admin_share_token():
    original_token = settings.ADMIN_SHARE_TOKEN
    try:
        yield
    finally:
        settings.ADMIN_SHARE_TOKEN = original_token


def test_admin_share_token_accepts_valid_token():
    settings.ADMIN_SHARE_TOKEN = "shortcut-secret"

    assert is_valid_admin_share_token("shortcut-secret") is True
    assert require_admin_share_token(x_admin_share_token="shortcut-secret") == "shortcut-secret"


def test_admin_share_token_rejects_missing_header():
    settings.ADMIN_SHARE_TOKEN = "shortcut-secret"

    with pytest.raises(HTTPException) as exc_info:
        require_admin_share_token(x_admin_share_token=None)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing X-Admin-Share-Token header"


def test_admin_share_token_rejects_invalid_value():
    settings.ADMIN_SHARE_TOKEN = "shortcut-secret"

    with pytest.raises(HTTPException) as exc_info:
        require_admin_share_token(x_admin_share_token="wrong-secret")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Invalid admin share token"


def test_admin_share_token_fails_closed_when_unconfigured():
    settings.ADMIN_SHARE_TOKEN = ""

    with pytest.raises(HTTPException) as exc_info:
        require_admin_share_token(x_admin_share_token="shortcut-secret")

    assert exc_info.value.status_code == 401
    assert (
        exc_info.value.detail
        == "Admin share-target endpoint is disabled (ADMIN_SHARE_TOKEN not configured)"
    )
