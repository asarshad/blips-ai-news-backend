"""Per-channel age policy helpers for videos and reels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, not_, or_, true

from app.integrations.youtube_channels import (
    get_channel_by_id,
    get_channel_by_name,
    get_enabled_channels,
)
from app.models.content import ContentItem


@dataclass(frozen=True)
class SurfaceAgePolicy:
    """Resolved age windows for a surface."""

    fresh_hours: int
    backfill_hours: int
    evergreen_days: int


@dataclass(frozen=True)
class SurfaceAgeFilters:
    """SQL filters for default + per-channel age windows."""

    fresh: Any
    backfill: Any
    evergreen_tier: Any
    reservoir: Any


@dataclass(frozen=True)
class _PolicyGroup:
    policy: SurfaceAgePolicy
    channel_ids: tuple[str, ...]
    source_names: tuple[str, ...]


def _resolve_policy_for_channel(
    channel_id: str | None,
    source_name: str | None,
    *,
    default_policy: SurfaceAgePolicy,
) -> SurfaceAgePolicy:
    cfg = None
    if channel_id:
        cfg = get_channel_by_id(channel_id)
    if cfg is None and source_name:
        cfg = get_channel_by_name(source_name)
    if cfg is None:
        return default_policy
    return SurfaceAgePolicy(
        fresh_hours=int(cfg.fresh_published_hours or default_policy.fresh_hours),
        backfill_hours=int(cfg.backfill_created_hours or default_policy.backfill_hours),
        evergreen_days=int(cfg.evergreen_max_days or default_policy.evergreen_days),
    )


def resolve_item_age_policy(
    item: Any,
    *,
    default_policy: SurfaceAgePolicy,
) -> SurfaceAgePolicy:
    """Resolve effective age windows for a content item."""
    channel_id = getattr(item, "channel_id", None)
    source_name = getattr(item, "source", None)
    return _resolve_policy_for_channel(channel_id, source_name, default_policy=default_policy)


def classify_item_age_bucket(
    item: Any,
    *,
    now: datetime,
    default_policy: SurfaceAgePolicy,
    evergreen_quality_floor: float = 0.3,
) -> str | None:
    """Classify an item into fresh/backfill/evergreen using per-channel policy."""
    policy = resolve_item_age_policy(item, default_policy=default_policy)
    published_at = getattr(item, "published_at", None)
    created_at = getattr(item, "created_at", None)
    global_score = float(getattr(item, "global_score", 0.0) or 0.0)
    if published_at is None:
        return None
    fresh_cutoff = now - timedelta(hours=policy.fresh_hours)
    backfill_cutoff = now - timedelta(hours=policy.backfill_hours)
    evergreen_cutoff = now - timedelta(days=policy.evergreen_days)
    if published_at >= fresh_cutoff:
        return "fresh"
    if created_at is not None and created_at >= backfill_cutoff:
        return "backfill"
    if published_at >= evergreen_cutoff and global_score >= evergreen_quality_floor:
        return "evergreen"
    return None


@lru_cache(maxsize=32)
def _override_groups(default_policy: SurfaceAgePolicy) -> tuple[_PolicyGroup, ...]:
    grouped: dict[SurfaceAgePolicy, dict[str, list[str]]] = {}
    for cfg in get_enabled_channels():
        if not cfg.has_age_overrides:
            continue
        policy = _resolve_policy_for_channel(
            cfg.channel_id, cfg.name, default_policy=default_policy
        )
        if policy == default_policy:
            continue
        bucket = grouped.setdefault(policy, {"channel_ids": [], "source_names": []})
        bucket["channel_ids"].append(cfg.channel_id)
        bucket["source_names"].append(cfg.name)
    return tuple(
        _PolicyGroup(
            policy=policy,
            channel_ids=tuple(sorted(set(values["channel_ids"]))),
            source_names=tuple(sorted(set(values["source_names"]))),
        )
        for policy, values in grouped.items()
    )


def _match_group(group: _PolicyGroup):
    clauses = []
    if group.channel_ids:
        clauses.append(
            and_(
                ContentItem.channel_id.isnot(None),
                ContentItem.channel_id.in_(group.channel_ids),
            )
        )
    if group.source_names:
        clauses.append(ContentItem.source.in_(group.source_names))
    if not clauses:
        return true()
    if len(clauses) == 1:
        return clauses[0]
    return or_(*clauses)


def build_surface_age_filters(
    *,
    now: datetime,
    default_policy: SurfaceAgePolicy,
) -> SurfaceAgeFilters:
    """Build SQLAlchemy filters that honor per-channel age overrides."""
    groups = _override_groups(default_policy)
    group_matches = [(_match_group(group), group.policy) for group in groups]
    if group_matches:
        override_match = or_(*(match for match, _policy in group_matches))
        default_match = not_(override_match)
    else:
        default_match = true()

    def _fresh_clause(match_expr, policy: SurfaceAgePolicy):
        return and_(
            match_expr,
            ContentItem.published_at >= now - timedelta(hours=policy.fresh_hours),
        )

    def _backfill_clause(match_expr, policy: SurfaceAgePolicy):
        fresh_cutoff = now - timedelta(hours=policy.fresh_hours)
        return and_(
            match_expr,
            ContentItem.created_at >= now - timedelta(hours=policy.backfill_hours),
            ContentItem.published_at < fresh_cutoff,
        )

    def _evergreen_clause(match_expr, policy: SurfaceAgePolicy):
        fresh_cutoff = now - timedelta(hours=policy.fresh_hours)
        evergreen_cutoff = now - timedelta(days=policy.evergreen_days)
        return and_(
            match_expr,
            ContentItem.published_at < fresh_cutoff,
            ContentItem.published_at >= evergreen_cutoff,
        )

    def _reservoir_clause(match_expr, policy: SurfaceAgePolicy):
        return and_(
            match_expr,
            ContentItem.published_at >= now - timedelta(days=policy.evergreen_days),
        )

    fresh_clauses = [_fresh_clause(default_match, default_policy)]
    backfill_clauses = [_backfill_clause(default_match, default_policy)]
    evergreen_clauses = [_evergreen_clause(default_match, default_policy)]
    reservoir_clauses = [_reservoir_clause(default_match, default_policy)]

    for match_expr, policy in group_matches:
        fresh_clauses.append(_fresh_clause(match_expr, policy))
        backfill_clauses.append(_backfill_clause(match_expr, policy))
        evergreen_clauses.append(_evergreen_clause(match_expr, policy))
        reservoir_clauses.append(_reservoir_clause(match_expr, policy))

    return SurfaceAgeFilters(
        fresh=or_(*fresh_clauses),
        backfill=or_(*backfill_clauses),
        evergreen_tier=or_(*evergreen_clauses),
        reservoir=or_(*reservoir_clauses),
    )


def make_default_policy(
    *, fresh_hours: int, backfill_hours: int, evergreen_days: int
) -> SurfaceAgePolicy:
    """Convenience helper for building a default policy from surface config."""
    return SurfaceAgePolicy(
        fresh_hours=int(fresh_hours),
        backfill_hours=int(backfill_hours),
        evergreen_days=int(evergreen_days),
    )


def make_item(
    *,
    channel_id: str | None,
    source: str,
    published_at: datetime,
    created_at: datetime,
    global_score: float,
) -> Any:
    """Test helper-friendly item builder."""
    return SimpleNamespace(
        channel_id=channel_id,
        source=source,
        published_at=published_at,
        created_at=created_at,
        global_score=global_score,
    )
