from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.integrations.youtube_channels import ChannelRole, QualityTier
from app.services.video_hybrid_rerank import rerank_video_candidates


def _item(
    *,
    item_id: int,
    source: str,
    title: str,
    hours_old: float,
    promotion_score: float,
    global_score: float,
    quality_score: float = 0.7,
    channel_id: str | None = None,
):
    published_at = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    return SimpleNamespace(
        id=item_id,
        source=source,
        channel_id=channel_id or source.lower().replace(" ", "-"),
        title=title,
        topics=[{"name": "Technology"}],
        entities=[{"name": "Apple"}],
        published_at=published_at,
        promotion_score=promotion_score,
        global_score=global_score,
        quality_score=quality_score,
        views_per_hour=180.0,
        description="Hands-on launch coverage",
        summary="Fresh launch coverage",
    )


def test_rerank_prefers_curated_recent_and_recent_escape_hatch(monkeypatch):
    curated_config = SimpleNamespace(
        role=ChannelRole.NEWS,
        quality_tier=QualityTier.PREMIUM,
    )
    monkeypatch.setattr(
        "app.services.video_hybrid_rerank.get_channel_by_name",
        lambda name: curated_config if name == "Curated Source" else None,
    )

    curated_recent = _item(
        item_id=1,
        source="Curated Source",
        title="iPhone Ultra review",
        hours_old=10,
        promotion_score=0.44,
        global_score=0.42,
    )
    off_roster_recent = _item(
        item_id=2,
        source="Independent Channel",
        title="MacBook Air hands on",
        hours_old=8,
        promotion_score=0.53,
        global_score=0.48,
    )
    off_roster_old = _item(
        item_id=3,
        source="Independent Archive",
        title="Laptop review roundup",
        hours_old=140,
        promotion_score=0.59,
        global_score=0.58,
    )

    reranked = rerank_video_candidates(
        [off_roster_old, off_roster_recent, curated_recent],
        target_count=3,
    )

    assert [item.id for item in reranked[:2]] == [1, 2]
    assert reranked[-1].id == 3


def test_rerank_limits_single_source_in_shaped_prefix(monkeypatch):
    curated_config = SimpleNamespace(
        role=ChannelRole.EXPLAINER,
        quality_tier=QualityTier.PREMIUM,
    )
    monkeypatch.setattr(
        "app.services.video_hybrid_rerank.get_channel_by_name",
        lambda _name: curated_config,
    )

    dominant = [
        _item(
            item_id=index,
            source="Dominant Source",
            title=f"Dominant review {index}",
            hours_old=6 + index,
            promotion_score=0.62 - index * 0.01,
            global_score=0.58 - index * 0.01,
        )
        for index in range(1, 6)
    ]
    alternative = [
        _item(
            item_id=100 + index,
            source=f"Alternative {index}",
            title=f"Alternative review {index}",
            hours_old=7 + index,
            promotion_score=0.51 - index * 0.01,
            global_score=0.49 - index * 0.01,
        )
        for index in range(1, 3)
    ]

    reranked = rerank_video_candidates(dominant + alternative, target_count=5)
    first_five_sources = [item.source for item in reranked[:5]]

    assert first_five_sources.count("Dominant Source") <= 4
    assert any(source.startswith("Alternative") for source in first_five_sources)


def test_rerank_reels_penalizes_repeated_creators_more_than_videos(monkeypatch):
    curated_config = SimpleNamespace(
        role=ChannelRole.EXPLAINER,
        quality_tier=QualityTier.PREMIUM,
    )
    monkeypatch.setattr(
        "app.services.video_hybrid_rerank.get_channel_by_name",
        lambda _name: curated_config,
    )

    dominant = [
        _item(
            item_id=index,
            source="Dominant Source",
            channel_id="dominant-channel",
            title=f"Dominant clip {index}",
            hours_old=3 + index,
            promotion_score=0.90 - index * 0.01,
            global_score=0.90 - index * 0.01,
        )
        for index in range(1, 5)
    ]
    alternatives = [
        _item(
            item_id=100 + index,
            source=f"Alternative {index}",
            channel_id=f"alt-{index}",
            title=f"Alternative clip {index}",
            hours_old=4 + index,
            promotion_score=0.20 - index * 0.01,
            global_score=0.20 - index * 0.01,
        )
        for index in range(1, 4)
    ]

    videos = rerank_video_candidates(dominant + alternatives, target_count=4, surface="videos")
    reels = rerank_video_candidates(dominant + alternatives, target_count=4, surface="reels")

    assert [item.source for item in videos[:4]].count("Dominant Source") >= 3
    assert [item.source for item in reels[:4]].count("Dominant Source") <= 2
