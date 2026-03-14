from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.models.content import ContentType
from app.services.inventory_service import FreshnessTier
from app.services.tiered_feed_service import (
    TieredItem,
    _cache_key,
    tiered_item_to_dict,
)


def test_tiered_item_to_dict_falls_back_to_video_description_when_summary_missing():
    item = SimpleNamespace(
        id=42,
        type=ContentType.VIDEO,
        title="Fresh video",
        source_url="https://www.youtube.com/watch?v=test123",
        video_url="https://www.youtube.com/watch?v=test123",
        summary=None,
        description="This is a detailed video description that should be used immediately while AI enrichment catches up.",
        image_url="https://img.youtube.com/vi/test123/maxresdefault.jpg",
        source="Trusted Source",
        created_at=datetime(2026, 3, 13, 12, 0, 0),
        published_at=datetime(2026, 3, 13, 11, 55, 0),
        global_score=0.44,
        promotion_score=0.37,
        recency_score=0.95,
        topics=["Technology"],
        channel_id="channel-1",
        acquisition_lane="search",
        source_status="discovery",
        views_per_hour=120.0,
        format_fit_score=1.0,
        promotion_reason="search|discovery",
        conversation_starters=None,
        duration_seconds=420,
    )

    tiered = TieredItem(
        item=item,
        tier=FreshnessTier.A,
        reason="fresh_published",
        published_age_seconds=300,
        added_age_seconds=60,
    )

    result = tiered_item_to_dict(tiered)

    assert result["summary"].startswith("This is a detailed video description")
    assert result["video_url"] == "https://www.youtube.com/watch?v=test123"
    assert result["conversation_starters"]["fallback"]


def test_cache_key_separates_hybrid_video_rerank_variants():
    base_key = _cache_key(
        surface=SimpleNamespace(value="videos"),
        limit=20,
        offset=0,
        require_ai=False,
        hybrid_video_rerank=False,
    )
    hybrid_key = _cache_key(
        surface=SimpleNamespace(value="videos"),
        limit=20,
        offset=0,
        require_ai=False,
        hybrid_video_rerank=True,
    )

    assert base_key != hybrid_key
    assert base_key.endswith("hybrid0")
    assert hybrid_key.endswith("hybrid1")
