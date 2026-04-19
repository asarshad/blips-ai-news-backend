from app.models.content import EventType
from app.services.feed_freshness_strategies import (
    ARTICLE_RECENT_HEAD_V1_STRATEGY,
    CURRENT_STRATEGY,
    FRESH_UNSEEN_V1_STRATEGY,
    FeedFreshnessStrategies,
)
from app.services.inventory_service import Surface


def test_feed_freshness_strategies_use_env_override(monkeypatch):
    monkeypatch.setenv("ARTICLES_FRESHNESS_STRATEGY", FRESH_UNSEEN_V1_STRATEGY)

    strategies = FeedFreshnessStrategies(redis_client=None)

    assert strategies.strategy_name(Surface.ARTICLES) == FRESH_UNSEEN_V1_STRATEGY
    assert strategies.strategy_source(Surface.ARTICLES) == "env"


def test_feed_freshness_strategies_default_articles_to_recent_head():
    strategies = FeedFreshnessStrategies(redis_client=None)

    assert strategies.strategy_name(Surface.ARTICLES) == ARTICLE_RECENT_HEAD_V1_STRATEGY
    assert strategies.strategy_name(Surface.VIDEOS) == ARTICLE_RECENT_HEAD_V1_STRATEGY
    assert strategies.strategy_name(Surface.REELS) == ARTICLE_RECENT_HEAD_V1_STRATEGY
    assert strategies.strategy_source(Surface.ARTICLES) == "default"
    assert strategies.strategy_source(Surface.VIDEOS) == "default"
    assert strategies.strategy_source(Surface.REELS) == "default"


def test_feed_freshness_strategies_fallback_to_current_for_unknown_env(monkeypatch):
    monkeypatch.setenv("ARTICLES_FRESHNESS_STRATEGY", "unknown_strategy")

    strategies = FeedFreshnessStrategies(redis_client=None)

    assert strategies.strategy_name(Surface.ARTICLES) == CURRENT_STRATEGY
    assert strategies.strategy_source(Surface.ARTICLES) == "invalid_fallback"


def test_fresh_unseen_v1_uses_view_10s_as_article_exposure_signal(monkeypatch):
    monkeypatch.setenv("ARTICLES_FRESHNESS_STRATEGY", FRESH_UNSEEN_V1_STRATEGY)

    strategy = FeedFreshnessStrategies(redis_client=None).resolve(Surface.ARTICLES)
    signals = strategy.feedback_signals(surface=Surface.ARTICLES)

    assert EventType.VIEW_10S in signals.exposed_event_types
    assert EventType.OPEN_SOURCE in signals.consumed_event_types
    assert strategy.resume_continuity_window_minutes(surface=Surface.ARTICLES) == 10
    assert strategy.resume_snapshot_after_remote_window(surface=Surface.ARTICLES) is False


def test_current_strategy_keeps_article_view_10s_out_of_exposed_events(monkeypatch):
    monkeypatch.setenv("ARTICLES_FRESHNESS_STRATEGY", CURRENT_STRATEGY)

    strategy = FeedFreshnessStrategies(redis_client=None).resolve(Surface.ARTICLES)
    signals = strategy.feedback_signals(surface=Surface.ARTICLES)

    assert EventType.VIEW_10S not in signals.exposed_event_types
    assert strategy.resume_continuity_window_minutes(surface=Surface.ARTICLES) is None
    assert strategy.resume_snapshot_after_remote_window(surface=Surface.ARTICLES) is True


def test_recent_head_strategy_uses_short_article_continuation_window():
    strategy = FeedFreshnessStrategies(redis_client=None).resolve(Surface.ARTICLES)
    signals = strategy.feedback_signals(surface=Surface.ARTICLES)

    assert EventType.VIEW_10S not in signals.exposed_event_types
    assert strategy.resume_continuity_window_minutes(surface=Surface.ARTICLES) == 10
    assert strategy.resume_snapshot_after_remote_window(surface=Surface.ARTICLES) is False


def test_current_strategy_orders_video_tier_a_by_day_then_score(monkeypatch):
    monkeypatch.setenv("VIDEOS_FRESHNESS_STRATEGY", CURRENT_STRATEGY)

    strategy = FeedFreshnessStrategies(redis_client=None).resolve(Surface.VIDEOS)
    clauses = strategy.tier_a_order_clauses(surface=Surface.VIDEOS, offset=0)
    rendered = [str(clause).lower() for clause in clauses]

    assert "date(" in rendered[0]
    assert "promotion_score" in rendered[1]
    assert "global_score" in rendered[2]
    assert "published_at" in rendered[3]
