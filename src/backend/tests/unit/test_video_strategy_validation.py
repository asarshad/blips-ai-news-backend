from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.video_strategy_validation import (
    ValidationItem,
    challenger_rank,
    strict_title_is_english,
    summarize_ranked,
    tech_signal_score,
)


def _item(
    *,
    item_id: int,
    surface: str,
    title: str,
    source: str,
    topics: list[str] | None = None,
    entities: list[str] | None = None,
    quality_score: float = 0.7,
    trend_score: float = 0.3,
    hours_old: int = 6,
) -> ValidationItem:
    return ValidationItem(
        id=item_id,
        surface=surface,
        title=title,
        source=source,
        source_url=f"https://www.youtube.com/watch?v=item-{item_id}",
        published_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_old),
        description=title,
        summary=title,
        topics=topics or ["Technology"],
        entities=entities or [],
        quality_score=quality_score,
        trend_score=trend_score,
        global_score=trend_score,
    )


def test_strict_title_is_english_rejects_non_latin_scripts():
    assert strict_title_is_english("Android 16 hands on")
    assert not strict_title_is_english("एंड्रॉइड 16 लॉन्च")


def test_tech_signal_score_penalizes_off_topic_terms():
    tech_item = _item(
        item_id=1,
        surface="reels",
        title="MacBook Air M4 hands on review",
        source="Waveform Clips",
        topics=["Technology"],
        entities=["MacBook Air"],
    )
    off_topic = _item(
        item_id=2,
        surface="reels",
        title="Family vlog challenge day",
        source="Waveform Clips",
        topics=["Lifestyle"],
    )

    assert tech_signal_score(tech_item) > tech_signal_score(off_topic)


def test_challenger_rank_prefers_curated_english_tech_items():
    items = [
        _item(
            item_id=1,
            surface="reels",
            title="MacBook Neo hands on",
            source="Waveform Clips",
            topics=["Technology"],
            entities=["MacBook Neo"],
        ),
        _item(
            item_id=2,
            surface="reels",
            title="एप्पल का नया फोन",
            source="Waveform Clips",
            topics=["Technology"],
        ),
        _item(
            item_id=3,
            surface="reels",
            title="Random prank compilation",
            source="Waveform Clips",
            topics=["Entertainment"],
        ),
        _item(
            item_id=4,
            surface="reels",
            title="Galaxy S26 update",
            source="Unknown Viral Clips",
            topics=["Technology"],
        ),
    ]

    ranked, excluded = challenger_rank(items, surface="reels", limit=10)

    assert [entry.item.id for entry in ranked] == [1]
    assert excluded["non_english_title"] == 1
    assert excluded["weak_tech_fit"] == 1
    assert excluded["off_roster"] == 1


def test_challenger_rank_favors_fresher_videos_when_quality_is_similar():
    items = [
        _item(
            item_id=10,
            surface="videos",
            title="MacBook Neo launch review",
            source="Marques Brownlee (MKBHD)",
            topics=["Technology"],
            entities=["MacBook Neo"],
            quality_score=0.8,
            trend_score=0.3,
            hours_old=18,
        ),
        _item(
            item_id=11,
            surface="videos",
            title="MacBook Neo review",
            source="Marques Brownlee (MKBHD)",
            topics=["Technology"],
            entities=["MacBook Neo"],
            quality_score=0.82,
            trend_score=0.32,
            hours_old=140,
        ),
    ]

    ranked, _excluded = challenger_rank(items, surface="videos", limit=10)

    assert [entry.item.id for entry in ranked[:2]] == [10, 11]


def test_challenger_rank_caps_reels_per_source():
    items = [
        _item(
            item_id=20 + index,
            surface="reels",
            title=f"iPhone 17e short update {index}",
            source="Waveform Clips",
            topics=["Technology"],
            entities=["iPhone 17e"],
            hours_old=6 + index,
        )
        for index in range(3)
    ] + [
        _item(
            item_id=30,
            surface="reels",
            title="Pixel 10 camera short",
            source="Android Authority",
            topics=["Technology"],
            entities=["Pixel 10"],
            hours_old=8,
        )
    ]

    ranked, _excluded = challenger_rank(items, surface="reels", limit=10)

    waveform_count = sum(1 for entry in ranked if entry.item.source == "Waveform Clips")
    assert waveform_count <= 2


def test_summarize_ranked_reports_curated_share_and_language_leaks():
    items = [
        _item(
            item_id=1,
            surface="videos",
            title="Pixel 10 launch update",
            source="Marques Brownlee (MKBHD)",
            topics=["Technology"],
        ),
        _item(
            item_id=2,
            surface="videos",
            title="एंड्रॉइड 16 लॉन्च",
            source="Unknown Channel",
            topics=["Technology"],
        ),
    ]

    summary = summarize_ranked(items)

    assert summary["count"] == 2
    assert summary["curated_share_pct"] == 50.0
    assert summary["non_english_titles"] == 1
    assert summary["distinct_sources"] == 2
