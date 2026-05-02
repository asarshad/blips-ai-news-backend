"""
Feed freshness strategy resolution and implementations.

Provides swappable per-surface freshness strategies with Redis-first,
env-fallback configuration so feed behavior can be changed without a deploy.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Set, Tuple

import redis
from redis.exceptions import RedisError
from sqlalchemy import desc, func

from app.core.logging import get_logger
from app.models.content import EventType
from app.services.inventory_service import Surface

logger = get_logger(__name__)

CURRENT_STRATEGY = "current"
FRESH_UNSEEN_V1_STRATEGY = "fresh_unseen_v1"
ARTICLE_RECENT_HEAD_V1_STRATEGY = "article_recent_head_v1"

REDIS_KEY_PREFIX = "blips:freshness_strategy:"
STRATEGY_CACHE_TTL_SECONDS = 10
STRATEGY_SOURCE_REDIS = "redis"
STRATEGY_SOURCE_ENV = "env"
STRATEGY_SOURCE_DEFAULT = "default"
STRATEGY_SOURCE_INVALID_FALLBACK = "invalid_fallback"

ARTICLE_CONSUMED_EVENTS = {
    EventType.OPEN_SOURCE,
    EventType.SHARE,
    EventType.SAVE,
    EventType.CHAT_START,
    EventType.CHAT_MESSAGE,
}
VIDEO_CONSUMED_EVENTS = ARTICLE_CONSUMED_EVENTS | {
    EventType.VIDEO_SAVE,
    EventType.VIDEO_SHARE,
    EventType.VIDEO_50PCT,
    EventType.VIDEO_95PCT,
}
VIDEO_EXPOSED_EVENTS = {EventType.VIDEO_IMPRESSION}

SURFACE_ENV_VARS = {
    Surface.ARTICLES: "ARTICLES_FRESHNESS_STRATEGY",
    Surface.VIDEOS: "VIDEOS_FRESHNESS_STRATEGY",
    Surface.REELS: "REELS_FRESHNESS_STRATEGY",
}

SURFACE_DEFAULTS = {
    Surface.ARTICLES: ARTICLE_RECENT_HEAD_V1_STRATEGY,
    Surface.VIDEOS: ARTICLE_RECENT_HEAD_V1_STRATEGY,
    Surface.REELS: ARTICLE_RECENT_HEAD_V1_STRATEGY,
}


@dataclass(frozen=True)
class FeedbackSignals:
    """Interaction events used for feed suppression or soft demotion."""

    consumed_event_types: Set[EventType]
    exposed_event_types: Set[EventType]


class FeedFreshnessStrategy(Protocol):
    """Contract for swappable feed freshness behavior."""

    name: str

    def tier_a_order_clauses(self, *, surface: Surface, offset: int) -> Tuple[Any, ...]:
        """Return SQLAlchemy order clauses for Tier A candidates."""

    def feedback_signals(self, *, surface: Surface) -> FeedbackSignals:
        """Return consumed/exposed event sets for the surface."""

    def resume_continuity_window_minutes(self, *, surface: Surface) -> Optional[int]:
        """Return session continuity window for supporting clients, if any."""

    def resume_snapshot_after_remote_window(self, *, surface: Surface) -> bool:
        """Whether clients may keep resuming a saved snapshot after remote continuity expires."""


class CurrentFeedFreshnessStrategy:
    name = CURRENT_STRATEGY

    def tier_a_order_clauses(self, *, surface: Surface, offset: int) -> Tuple[Any, ...]:
        from app.models.content import ContentItem

        if surface in (Surface.VIDEOS, Surface.REELS):
            return (
                desc(func.date(ContentItem.published_at)),
                desc(ContentItem.promotion_score),
                desc(ContentItem.global_score),
                desc(ContentItem.published_at),
            )
        return (
            desc(ContentItem.global_score),
            desc(ContentItem.published_at),
        )

    def feedback_signals(self, *, surface: Surface) -> FeedbackSignals:
        if surface == Surface.ARTICLES:
            return FeedbackSignals(
                consumed_event_types=ARTICLE_CONSUMED_EVENTS,
                exposed_event_types=set(),
            )
        return FeedbackSignals(
            consumed_event_types=VIDEO_CONSUMED_EVENTS,
            exposed_event_types=VIDEO_EXPOSED_EVENTS,
        )

    def resume_continuity_window_minutes(self, *, surface: Surface) -> Optional[int]:
        return None

    def resume_snapshot_after_remote_window(self, *, surface: Surface) -> bool:
        return True


class FreshUnseenV1FeedFreshnessStrategy(CurrentFeedFreshnessStrategy):
    name = FRESH_UNSEEN_V1_STRATEGY

    def tier_a_order_clauses(self, *, surface: Surface, offset: int) -> Tuple[Any, ...]:
        from app.models.content import ContentItem

        if surface != Surface.ARTICLES:
            return super().tier_a_order_clauses(surface=surface, offset=offset)

        # Bias the first page toward fresh head rotation while still retaining
        # score-based ordering for deeper pagination.
        if offset <= 0:
            return (
                desc(ContentItem.published_at),
                desc(ContentItem.global_score),
                desc(ContentItem.promotion_score),
            )
        return (
            desc(ContentItem.global_score),
            desc(ContentItem.published_at),
        )

    def feedback_signals(self, *, surface: Surface) -> FeedbackSignals:
        if surface == Surface.ARTICLES:
            return FeedbackSignals(
                consumed_event_types=ARTICLE_CONSUMED_EVENTS,
                exposed_event_types={EventType.VIEW_10S},
            )
        return super().feedback_signals(surface=surface)

    def resume_continuity_window_minutes(self, *, surface: Surface) -> Optional[int]:
        if surface == Surface.ARTICLES:
            return 10
        return None

    def resume_snapshot_after_remote_window(self, *, surface: Surface) -> bool:
        if surface == Surface.ARTICLES:
            return False
        return True


class ArticleRecentHeadV1FeedFreshnessStrategy(CurrentFeedFreshnessStrategy):
    name = ARTICLE_RECENT_HEAD_V1_STRATEGY

    def tier_a_order_clauses(self, *, surface: Surface, offset: int) -> Tuple[Any, ...]:
        from app.models.content import ContentItem

        if surface != Surface.ARTICLES:
            return super().tier_a_order_clauses(surface=surface, offset=offset)

        return (
            desc(func.date(ContentItem.published_at)),
            desc(ContentItem.promotion_score),
            desc(ContentItem.global_score),
            desc(ContentItem.published_at),
        )

    def resume_continuity_window_minutes(self, *, surface: Surface) -> Optional[int]:
        if surface == Surface.ARTICLES:
            return 10
        return None

    def resume_snapshot_after_remote_window(self, *, surface: Surface) -> bool:
        if surface == Surface.ARTICLES:
            return False
        return True


STRATEGIES: Dict[str, FeedFreshnessStrategy] = {
    CURRENT_STRATEGY: CurrentFeedFreshnessStrategy(),
    FRESH_UNSEEN_V1_STRATEGY: FreshUnseenV1FeedFreshnessStrategy(),
    ARTICLE_RECENT_HEAD_V1_STRATEGY: ArticleRecentHeadV1FeedFreshnessStrategy(),
}


class FeedFreshnessStrategies:
    """Resolve per-surface freshness strategies with Redis/env fallback."""

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self._redis = redis_client
        self._cache: Dict[str, Tuple[str, str, float]] = {}

    def _get_redis(self) -> Optional[redis.Redis]:
        if self._redis is None:
            try:
                from app.core.dependencies import get_redis

                self._redis = get_redis()
                self._redis.ping()
            except RedisError as exc:
                logger.warning("Redis unavailable for freshness strategies: %s", exc)
                self._redis = None
        return self._redis

    def _surface_key(self, surface: Surface) -> str:
        return surface.value

    def _redis_key(self, surface: Surface) -> str:
        return f"{REDIS_KEY_PREFIX}{surface.value}"

    def _get_from_redis(self, surface: Surface) -> Optional[str]:
        redis_client = self._get_redis()
        if redis_client is None:
            return None

        try:
            value = redis_client.get(self._redis_key(surface))
            if value is None:
                return None
            if isinstance(value, bytes):
                return value.decode().strip().lower() or None
            return str(value).strip().lower() or None
        except RedisError as exc:
            logger.warning("Error reading freshness strategy from Redis: %s", exc)
            return None

    def _get_from_env(self, surface: Surface) -> Optional[str]:
        env_var = SURFACE_ENV_VARS[surface]
        value = os.getenv(env_var)
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    def _record_resolution_metric(self, *, surface: Surface, source: str) -> None:
        from app.services.freshness_metrics_service import record_client_freshness_event

        metric = {
            STRATEGY_SOURCE_REDIS: "strategy_resolved_redis",
            STRATEGY_SOURCE_ENV: "strategy_resolved_env",
            STRATEGY_SOURCE_DEFAULT: "strategy_resolved_default",
            STRATEGY_SOURCE_INVALID_FALLBACK: "strategy_fallback_unknown",
        }.get(source)
        if metric:
            record_client_freshness_event(event_name=metric, surface=surface.value)

    def resolution(self, surface: Surface) -> Tuple[str, str]:
        cache_key = self._surface_key(surface)
        cached = self._cache.get(cache_key)
        if cached is not None:
            value, source, expiry = cached
            if time.monotonic() < expiry:
                return value, source

        source = STRATEGY_SOURCE_DEFAULT
        resolved = self._get_from_redis(surface)
        if resolved is not None:
            source = STRATEGY_SOURCE_REDIS
        else:
            resolved = self._get_from_env(surface)
            if resolved is not None:
                source = STRATEGY_SOURCE_ENV
            else:
                resolved = SURFACE_DEFAULTS[surface]
        if resolved not in STRATEGIES:
            logger.warning(
                "Unknown freshness strategy '%s' for surface=%s; falling back to '%s'",
                resolved,
                surface.value,
                CURRENT_STRATEGY,
            )
            resolved = CURRENT_STRATEGY
            source = STRATEGY_SOURCE_INVALID_FALLBACK

        self._cache[cache_key] = (
            resolved,
            source,
            time.monotonic() + STRATEGY_CACHE_TTL_SECONDS,
        )
        self._record_resolution_metric(surface=surface, source=source)
        return resolved, source

    def strategy_name(self, surface: Surface) -> str:
        return self.resolution(surface)[0]

    def strategy_source(self, surface: Surface) -> str:
        return self.resolution(surface)[1]

    def resolve(self, surface: Surface) -> FeedFreshnessStrategy:
        return STRATEGIES[self.resolution(surface)[0]]

    def for_name(self, strategy_name: Optional[str]) -> FeedFreshnessStrategy:
        if not strategy_name:
            return STRATEGIES[CURRENT_STRATEGY]
        normalized = str(strategy_name).strip().lower()
        return STRATEGIES.get(normalized, STRATEGIES[CURRENT_STRATEGY])


_strategies: Optional[FeedFreshnessStrategies] = None


def get_feed_freshness_strategies() -> FeedFreshnessStrategies:
    global _strategies
    if _strategies is None:
        _strategies = FeedFreshnessStrategies()
    return _strategies


class _LazyFeedFreshnessStrategies:
    _instance: Optional[FeedFreshnessStrategies] = None

    def __getattr__(self, name: str):
        if self._instance is None:
            self._instance = FeedFreshnessStrategies()
        return getattr(self._instance, name)


feed_freshness_strategies: FeedFreshnessStrategies = _LazyFeedFreshnessStrategies()  # type: ignore[assignment]
