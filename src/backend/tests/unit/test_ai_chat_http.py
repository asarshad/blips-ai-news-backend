from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace

import fakeredis
import httpx
from fastapi.testclient import TestClient
from openai import BadRequestError

from app.core.dependencies import get_db, get_redis
from app.core.feature_flags import get_feature_flags
from app.core.session_auth import AuthenticatedSession, require_session_token
from app.integrations.llm_client import ChatResponse, LLMClient
from app.repositories.content_repo import ContentItemRepository
from app.repositories.usage_repo import UsageRepository


def _enable_test_app():
    os.environ["SKIP_STARTUP_CHECKS"] = "true"
    os.environ["SKIP_CREATE_TABLES"] = "true"
    os.environ["SCHEDULER_ENABLED"] = "false"

    from app.main import app

    return app


def _override_db():
    class _FakeDB:
        def commit(self):
            return None

        def rollback(self):
            return None

    yield _FakeDB()


def _override_session() -> AuthenticatedSession:
    return AuthenticatedSession(
        device_id="device-http-test",
        platform="ios",
        app_version="1.0.0",
        session_expires_at=datetime.max.replace(tzinfo=UTC),
    )


def _override_flags():
    return SimpleNamespace(is_enabled=lambda feature: feature == "chat")


def _previous_response_not_found_error() -> BadRequestError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(
        400,
        request=request,
        json={
            "error": {
                "message": "Previous response with id 'resp_prev' not found.",
                "type": "invalid_request_error",
                "param": "previous_response_id",
                "code": "previous_response_not_found",
            }
        },
    )
    return BadRequestError(
        "Previous response with id 'resp_prev' not found.",
        response=response,
        body=response.json(),
    )


def _build_client(monkeypatch, fake_chat, *, content_item=None):
    app = _enable_test_app()
    import app.main as main_module

    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    content_item = content_item or SimpleNamespace(
        id=480488,
        title="Test article",
        summary="Summary",
        conversation_starters=None,
        starter_answers=None,
    )

    monkeypatch.setattr(ContentItemRepository, "get_by_id", lambda self, _id: content_item)
    monkeypatch.setattr(UsageRepository, "get_daily_usage", lambda self, _device_id: 0)
    monkeypatch.setattr(UsageRepository, "get_article_usage", lambda self, _device_id, _article_id: 0)
    monkeypatch.setattr(UsageRepository, "record_usage", lambda self, *_args, **_kwargs: None)
    monkeypatch.setattr(LLMClient, "__init__", lambda self, provider=None, api_key=None, model=None: None)
    monkeypatch.setattr(LLMClient, "get_provider", lambda self: "openai")
    monkeypatch.setattr(LLMClient, "chat", fake_chat)
    monkeypatch.setattr(main_module, "get_redis", lambda: fake_redis)
    main_module._redis_healthy = True
    main_module._redis_last_check = 0.0

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_feature_flags] = _override_flags
    app.dependency_overrides[require_session_token] = _override_session

    return app


def test_ai_chat_http_followup_chains_previous_response_id(monkeypatch):
    calls = []

    def fake_chat(self, messages, max_tokens=300, temperature=0.7, previous_response_id=None, store=False):
        calls.append(
            {
                "roles": [message.role for message in messages],
                "previous_response_id": previous_response_id,
                "store": store,
            }
        )
        if previous_response_id:
            return ChatResponse(
                content="second answer",
                tokens_used=22,
                model="gpt-5-mini",
                provider="openai",
                response_id="resp_next",
            )
        return ChatResponse(
            content="first answer",
            tokens_used=17,
            model="gpt-5-mini",
            provider="openai",
            response_id="resp_first",
        )

    app = _build_client(monkeypatch, fake_chat)

    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/ai/respond",
                json={
                    "content_item_id": 480488,
                    "message": "Tell me more",
                    "sender": "user",
                },
            )
            second = client.post(
                "/api/v1/ai/respond",
                json={
                    "content_item_id": 480488,
                    "message": "What else?",
                    "sender": "user",
                    "history": [
                        {"role": "user", "content": "Tell me more"},
                        {"role": "ai", "content": "first answer"},
                    ],
                    "previous_response_id": "resp_first",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert first.json()["response_id"] == "resp_first"
    assert second.status_code == 200
    assert second.json()["response"] == "second answer"
    assert second.json()["response_id"] == "resp_next"
    assert calls[1]["previous_response_id"] == "resp_first"
    assert calls[1]["roles"] == ["system", "user"]
    assert calls[1]["store"] is True


def test_ai_chat_http_followup_replays_history_when_previous_response_id_is_missing(monkeypatch):
    calls = []

    def fake_chat(self, messages, max_tokens=300, temperature=0.7, previous_response_id=None, store=False):
        calls.append(
            {
                "roles": [message.role for message in messages],
                "previous_response_id": previous_response_id,
                "store": store,
            }
        )
        if previous_response_id:
            raise _previous_response_not_found_error()
        return ChatResponse(
            content="fallback answer",
            tokens_used=19,
            model="gpt-5-mini",
            provider="openai",
            response_id="resp_replayed",
        )

    app = _build_client(monkeypatch, fake_chat)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/ai/respond",
                json={
                    "content_item_id": 480488,
                    "message": "What else?",
                    "sender": "user",
                    "history": [
                        {"role": "user", "content": "Tell me more"},
                        {"role": "ai", "content": "first answer"},
                    ],
                    "previous_response_id": "resp_prev",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["response"] == "fallback answer"
    assert response.json()["response_id"] == "resp_replayed"
    assert calls[0]["previous_response_id"] == "resp_prev"
    assert calls[1]["previous_response_id"] is None
    assert calls[1]["roles"] == ["system", "user", "assistant", "user"]
    assert calls[1]["store"] is True


def test_ai_chat_http_uses_cached_starter_answer_without_quota_burn(monkeypatch):
    content_item = SimpleNamespace(
        id=480488,
        title="Test article",
        summary="Summary",
        conversation_starters={"starters": ["What are the key takeaways?"]},
        starter_answers={"What are the key takeaways?": "Cached starter answer"},
    )

    def fake_chat(self, messages, max_tokens=300, temperature=0.7, previous_response_id=None, store=False):
        raise AssertionError("LLM should not be called for cached starter responses")

    app = _build_client(monkeypatch, fake_chat, content_item=content_item)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/ai/respond",
                json={
                    "content_item_id": 480488,
                    "message": "What are the key takeaways?",
                    "sender": "user",
                    "starter_prompt": True,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["response"] == "Cached starter answer"
    assert response.json()["response_id"] is None
    assert response.json()["used_cached_starter_response"] is True
    assert response.json()["remaining_daily"] == 15
    assert response.json()["remaining_article"] == 5


def test_ai_chat_http_generates_and_persists_starter_answer_on_cache_miss(monkeypatch):
    content_item = SimpleNamespace(
        id=480488,
        title="Test article",
        summary="Summary",
        conversation_starters={"starters": ["What are the key takeaways?"]},
        starter_answers=None,
    )
    calls = []
    record_calls = []

    def fake_chat(self, messages, max_tokens=300, temperature=0.7, previous_response_id=None, store=False):
        calls.append(
            {
                "roles": [message.role for message in messages],
                "previous_response_id": previous_response_id,
                "store": store,
            }
        )
        return ChatResponse(
            content="Freshly generated starter answer",
            tokens_used=21,
            model="gpt-5-mini",
            provider="openai",
            response_id="resp_generated",
        )

    app = _build_client(monkeypatch, fake_chat, content_item=content_item)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/ai/respond",
                json={
                    "content_item_id": 480488,
                    "message": "What are the key takeaways?",
                    "sender": "user",
                    "starter_prompt": True,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["response"] == "Freshly generated starter answer"
    assert response.json()["response_id"] == "resp_generated"
    assert response.json()["used_cached_starter_response"] is False
    assert response.json()["remaining_daily"] == 14
    assert response.json()["remaining_article"] == 4
    assert len(calls) == 1
    assert calls[0]["roles"] == ["system", "user"]
    assert calls[0]["previous_response_id"] is None
    assert content_item.starter_answers == {
        "What are the key takeaways?": "Freshly generated starter answer"
    }


def test_ai_chat_http_does_not_use_cached_starter_answer_without_starter_flag(monkeypatch):
    content_item = SimpleNamespace(
        id=480488,
        title="Test article",
        summary="Summary",
        conversation_starters={"starters": ["What are the key takeaways?"]},
        starter_answers={"What are the key takeaways?": "Cached starter answer"},
    )
    calls = []
    def fake_chat(self, messages, max_tokens=300, temperature=0.7, previous_response_id=None, store=False):
        calls.append(
            {
                "roles": [message.role for message in messages],
                "previous_response_id": previous_response_id,
                "store": store,
            }
        )
        return ChatResponse(
            content="Fresh answer for typed message",
            tokens_used=18,
            model="gpt-5-mini",
            provider="openai",
            response_id="resp_typed",
        )

    app = _build_client(monkeypatch, fake_chat, content_item=content_item)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/ai/respond",
                json={
                    "content_item_id": 480488,
                    "message": "What are the key takeaways?",
                    "sender": "user",
                    "starter_prompt": False,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["response"] == "Fresh answer for typed message"
    assert response.json()["used_cached_starter_response"] is False
    assert response.json()["remaining_daily"] == 14
    assert response.json()["remaining_article"] == 4
    assert len(calls) == 1
    assert calls[0]["roles"] == ["system", "user"]


def test_ai_chat_http_stores_generated_starter_answer_on_cache_miss(monkeypatch):
    content_item = SimpleNamespace(
        id=480488,
        title="Test article",
        summary="Summary",
        conversation_starters={"starters": ["What are the key takeaways?"]},
        starter_answers=None,
    )

    def fake_chat(self, messages, max_tokens=300, temperature=0.7, previous_response_id=None, store=False):
        return ChatResponse(
            content="Fresh starter answer",
            tokens_used=17,
            model="gpt-5-mini",
            provider="openai",
            response_id="resp_first",
        )

    app = _build_client(monkeypatch, fake_chat, content_item=content_item)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/ai/respond",
                json={
                    "content_item_id": 480488,
                    "message": "What are the key takeaways?",
                    "sender": "user",
                    "starter_prompt": True,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["response"] == "Fresh starter answer"
    assert response.json()["used_cached_starter_response"] is False
    assert content_item.starter_answers == {
        "What are the key takeaways?": "Fresh starter answer",
    }


def test_ai_chat_http_quota_error_keeps_legacy_detail_and_exposes_quota_metadata(monkeypatch):
    def fake_chat(self, messages, max_tokens=300, temperature=0.7, previous_response_id=None, store=False):
        raise AssertionError("LLM should not be called when quota is exhausted")

    app = _build_client(monkeypatch, fake_chat)
    monkeypatch.setattr(UsageRepository, "get_daily_usage", lambda self, _device_id: 15)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/ai/respond",
                json={
                    "content_item_id": 480488,
                    "message": "Tell me more",
                    "sender": "user",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 429
    assert response.json() == {
        "detail": "Daily message quota exceeded",
        "quota_type": "daily",
        "message": "Daily message quota exceeded",
    }
