from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import articles as articles_module
from app.models.content import ContentStatus, ContentType


def test_get_article_rejects_unready_article():
    item = SimpleNamespace(
        id=11,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        source_url="https://example.com/article",
        canonical_url="https://example.com/article",
        ai_processed=False,
        summary=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        articles_module.get_article(
            article_id=11,
            content_repo=SimpleNamespace(get_by_id=lambda _article_id: item),
        )

    assert exc_info.value.status_code == 404


def test_get_article_returns_ready_article_payload():
    now = datetime(2026, 3, 23, 12, 0, 0)
    item = SimpleNamespace(
        id=12,
        type=ContentType.ARTICLE,
        curation_status=ContentStatus.PROMOTED,
        is_suppressed=False,
        promotion_reason=None,
        title="Ready article",
        source="Blips",
        source_url="https://example.com/ready",
        canonical_url="https://example.com/ready",
        summary="This article is fully hydrated and ready.",
        image_url=None,
        published_at=now,
        created_at=now,
        ai_processed=True,
        topics=["AI"],
        conversation_starters={"starters": ["Why now?"], "fallback": ["Summarize it"]},
    )

    payload = articles_module.get_article(
        article_id=12,
        content_repo=SimpleNamespace(get_by_id=lambda _article_id: item),
    )

    assert payload["id"] == 12
    assert payload["summary"] == "This article is fully hydrated and ready."
