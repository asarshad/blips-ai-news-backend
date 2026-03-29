from __future__ import annotations

from datetime import timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import session_auth as session_auth_module
from app.models.content import UserProfile
from app.models.device_session import DeviceSession


def _db_session():
    engine = create_engine("sqlite:///:memory:")
    UserProfile.__table__.create(engine)
    DeviceSession.__table__.create(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_create_refresh_and_revoke_session(monkeypatch):
    monkeypatch.setattr(session_auth_module.settings, "ENV", "test")
    monkeypatch.setattr(
        session_auth_module.settings,
        "SESSION_AUTH_ACCESS_SECRET",
        "test-secret-key-with-at-least-thirty-two-bytes",
    )

    db = _db_session()
    response = session_auth_module.create_session(
        db,
        platform="ios",
        app_version="1.2.3",
    )

    claims = session_auth_module._decode_access_token(response.access_token)
    assert claims["sub"] == response.device_id

    refreshed = session_auth_module.refresh_session(db, response.refresh_token)
    assert refreshed.device_id == response.device_id
    assert refreshed.refresh_token != response.refresh_token

    session = session_auth_module.require_session_token(
        authorization=f"Bearer {refreshed.access_token}",
        db=db,
    )
    assert session.device_id == response.device_id
    assert session.session_expires_at.tzinfo == timezone.utc

    session_auth_module.revoke_session(
        db,
        device_id=response.device_id,
        refresh_token=refreshed.refresh_token,
    )

    try:
        session_auth_module.refresh_session(db, refreshed.refresh_token)
    except session_auth_module.SessionAuthError as exc:
        assert exc.status_code == 401
    else:  # pragma: no cover
        raise AssertionError("Expected revoked session to reject refresh")


def test_access_secret_requires_explicit_config_in_prod(monkeypatch):
    monkeypatch.setattr(session_auth_module.settings, "ENV", "prod")
    monkeypatch.setattr(session_auth_module.settings, "SESSION_AUTH_ACCESS_SECRET", "")

    db = _db_session()

    try:
        session_auth_module.create_session(
            db,
            platform="ios",
            app_version="1.2.3",
        )
    except session_auth_module.SessionAuthError as exc:
        assert exc.status_code == 503
        assert exc.detail == "Session auth is not configured"
    else:  # pragma: no cover
        raise AssertionError("Expected prod session creation to require a configured secret")
