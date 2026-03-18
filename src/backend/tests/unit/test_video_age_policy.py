from __future__ import annotations

from datetime import datetime, timedelta

from app.video_age_policy import (
    SurfaceAgePolicy,
    classify_item_age_bucket,
    make_item,
    resolve_item_age_policy,
)


def test_resolve_item_age_policy_uses_channel_override_for_podcast_source():
    default_policy = SurfaceAgePolicy(fresh_hours=168, backfill_hours=72, evergreen_days=30)
    item = make_item(
        channel_id="UCXl4i9dYBrFOabk0xGmbkRA",
        source="Dwarkesh Podcast",
        published_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        global_score=0.5,
    )

    policy = resolve_item_age_policy(item, default_policy=default_policy)

    assert policy.fresh_hours == 72
    assert policy.backfill_hours == 24
    assert policy.evergreen_days == 21


def test_classify_item_age_bucket_respects_shorter_podcast_fresh_window():
    now = datetime(2026, 3, 18, 20, 0, 0)
    default_policy = SurfaceAgePolicy(fresh_hours=168, backfill_hours=72, evergreen_days=30)

    podcast_item = make_item(
        channel_id="UCXl4i9dYBrFOabk0xGmbkRA",
        source="Dwarkesh Podcast",
        published_at=now - timedelta(days=4),
        created_at=now - timedelta(days=2),
        global_score=0.7,
    )
    regular_item = make_item(
        channel_id=None,
        source="Generic Tech Source",
        published_at=now - timedelta(days=4),
        created_at=now - timedelta(days=2),
        global_score=0.7,
    )

    assert (
        classify_item_age_bucket(
            podcast_item,
            now=now,
            default_policy=default_policy,
        )
        == "evergreen"
    )
    assert (
        classify_item_age_bucket(
            regular_item,
            now=now,
            default_policy=default_policy,
        )
        == "fresh"
    )
