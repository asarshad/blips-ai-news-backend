from __future__ import annotations

from datetime import datetime, timedelta

from app.config.video_discovery import discovery_cutoff, get_query_packs


def test_discovery_queries_no_longer_use_today_keyword():
    video_queries = [pack.query for pack in get_query_packs("videos")]
    reel_queries = [pack.query for pack in get_query_packs("reels")]

    assert all("today" not in query.lower() for query in video_queries)
    assert all("today" not in query.lower() for query in reel_queries)


def test_discovery_cutoff_uses_weekly_window():
    cutoff = discovery_cutoff("videos")
    now = datetime.utcnow()

    assert now - timedelta(days=8) < cutoff < now - timedelta(days=6)


def test_query_packs_use_large_result_pages():
    assert all(pack.max_results == 25 for pack in get_query_packs("videos"))
    assert all(pack.max_results == 25 for pack in get_query_packs("reels"))
