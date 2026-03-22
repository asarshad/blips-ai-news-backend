"""Runtime freshness metrics and analytics helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus, ContentType, EventType, InteractionEvent
from app.services.inventory_service import FreshnessTier, Surface, _get_surface_config

_COUNTER_KEYS = {
    "resume_position_restored",
    "new_content_available",
    "new_content_opened",
    "feed_version_changed",
    "stale_feed_served",
    "topup_triggered",
    "topup_completed",
    "feed_served",
}

_runtime_lock = Lock()
_runtime_counters: DefaultDict[str, Counter[str]] = defaultdict(Counter)
_last_feed_versions: Dict[str, str] = {}
_topup_state: Dict[str, Any] = {
    "last_triggered_at": None,
    "last_completed_at": None,
    "last_duration_seconds": None,
    "last_cycles": None,
    "last_priority_surfaces": [],
}


def _counter_bucket(surface: Optional[str]) -> List[str]:
    bucket = ["all"]
    if surface:
        bucket.append(surface)
    return bucket


def _increment(metric: str, *, surface: Optional[str] = None, amount: int = 1) -> None:
    if metric not in _COUNTER_KEYS or amount <= 0:
        return
    with _runtime_lock:
        for bucket in _counter_bucket(surface):
            _runtime_counters[bucket][metric] += amount


def _surface_for_item(item: ContentItem) -> Surface:
    if item.type == ContentType.REEL:
        return Surface.REELS
    if item.type == ContentType.VIDEO:
        return Surface.VIDEOS
    return Surface.ARTICLES


def _classify_item(item: ContentItem, now: datetime) -> Optional[FreshnessTier]:
    surface = _surface_for_item(item)
    cfg = _get_surface_config(surface)

    fresh_cutoff = now - timedelta(hours=cfg["fresh_hours"])
    backfill_cutoff = now - timedelta(hours=cfg["backfill_hours"])
    evergreen_cutoff = now - timedelta(days=cfg["evergreen_days"])

    published_at = item.published_at
    created_at = item.created_at
    if published_at and published_at >= fresh_cutoff:
        return FreshnessTier.A
    if (
        created_at
        and created_at >= backfill_cutoff
        and published_at
        and published_at < fresh_cutoff
    ):
        return FreshnessTier.B
    if (
        published_at
        and published_at < fresh_cutoff
        and published_at >= evergreen_cutoff
        and float(item.global_score or 0.0) >= 0.3
    ):
        return FreshnessTier.C
    return None


def record_feed_served(
    *,
    surface: str,
    feed_version: Optional[str],
    inventory_state: Optional[str],
    items: Iterable[Dict[str, Any]],
) -> None:
    """Track runtime freshness counters for feed responses."""
    items_list = list(items)
    _increment("feed_served", surface=surface)

    fresh_count = sum(
        1 for item in items_list if item.get("freshness_tier") == FreshnessTier.A.value
    )
    if inventory_state not in (None, "healthy") or fresh_count == 0:
        _increment("stale_feed_served", surface=surface)

    if not feed_version:
        return

    with _runtime_lock:
        previous = _last_feed_versions.get(surface)
        if previous and previous != feed_version:
            for bucket in _counter_bucket(surface):
                _runtime_counters[bucket]["feed_version_changed"] += 1
        _last_feed_versions[surface] = feed_version


def record_client_freshness_event(
    *,
    event_name: str,
    surface: Optional[str],
    count: int = 1,
) -> None:
    """Track client-emitted freshness events."""
    _increment(event_name, surface=surface, amount=count)


def record_topup_triggered(priority_surfaces: Optional[Iterable[str]] = None) -> None:
    surfaces = list(priority_surfaces or [])
    if surfaces:
        for surface in surfaces:
            _increment("topup_triggered", surface=surface)
    else:
        _increment("topup_triggered")

    with _runtime_lock:
        _topup_state["last_triggered_at"] = datetime.utcnow().isoformat()
        _topup_state["last_priority_surfaces"] = surfaces


def record_topup_completed(
    *,
    priority_surfaces: Optional[Iterable[str]] = None,
    duration_seconds: Optional[float],
    cycles: Optional[int],
) -> None:
    surfaces = list(priority_surfaces or [])
    if surfaces:
        for surface in surfaces:
            _increment("topup_completed", surface=surface)
    else:
        _increment("topup_completed")

    with _runtime_lock:
        _topup_state["last_completed_at"] = datetime.utcnow().isoformat()
        _topup_state["last_duration_seconds"] = round(duration_seconds or 0.0, 2)
        _topup_state["last_cycles"] = cycles
        _topup_state["last_priority_surfaces"] = surfaces


def _estimated_dwell_seconds(event_type: EventType, item: ContentItem) -> float:
    if event_type == EventType.VIEW_10S:
        return 10.0
    if event_type == EventType.VIDEO_3S:
        return 3.0
    if event_type == EventType.VIDEO_50PCT:
        return max(float(item.duration_seconds or 0) * 0.5, 15.0)
    if event_type == EventType.VIDEO_95PCT:
        return max(float(item.duration_seconds or 0) * 0.95, 25.0)
    return 0.0


def _interaction_metrics_by_tier(db: Session, *, hours: int) -> Dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    rows: List[Tuple[InteractionEvent, ContentItem]] = (
        db.query(InteractionEvent, ContentItem)
        .join(ContentItem, ContentItem.id == InteractionEvent.content_item_id)
        .filter(
            InteractionEvent.created_at >= cutoff,
            ContentItem.curation_status == ContentStatus.PROMOTED,
            ContentItem.is_suppressed.is_(False),
        )
        .all()
    )

    now = datetime.utcnow()
    result: Dict[str, Dict[str, Dict[str, float]]] = {}

    for event, item in rows:
        surface = _surface_for_item(item).value
        tier = _classify_item(item, now)
        if tier is None:
            continue
        surface_metrics = result.setdefault(surface, {})
        tier_metrics = surface_metrics.setdefault(
            tier.value,
            {
                "events": 0,
                "opens": 0,
                "shares": 0,
                "saves": 0,
                "estimated_dwell_seconds": 0.0,
            },
        )
        tier_metrics["events"] += 1
        if event.event_type == EventType.OPEN_SOURCE:
            tier_metrics["opens"] += 1
        elif event.event_type in {EventType.SHARE, EventType.VIDEO_SHARE}:
            tier_metrics["shares"] += 1
        elif event.event_type in {EventType.SAVE, EventType.VIDEO_SAVE}:
            tier_metrics["saves"] += 1
        tier_metrics["estimated_dwell_seconds"] += _estimated_dwell_seconds(event.event_type, item)

    return result


def _distribution_metrics(db: Session, *, hours: int) -> Dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    items = (
        db.query(ContentItem)
        .filter(
            ContentItem.curation_status == ContentStatus.PROMOTED,
            ContentItem.is_suppressed.is_(False),
            ContentItem.published_at >= cutoff,
        )
        .all()
    )

    surfaces: Dict[str, Dict[str, Counter[str]]] = defaultdict(
        lambda: {
            "categories": Counter(),
            "sources": Counter(),
        }
    )

    for item in items:
        surface = _surface_for_item(item).value
        primary_topic = (item.topics or ["Technology"])[0] or "Technology"
        source = item.source or "Unknown"
        surfaces[surface]["categories"][str(primary_topic)] += 1
        surfaces[surface]["sources"][str(source)] += 1

    payload: Dict[str, Any] = {}
    for surface, counters in surfaces.items():
        category_total = max(sum(counters["categories"].values()), 1)
        source_total = max(sum(counters["sources"].values()), 1)
        payload[surface] = {
            "category_share_percent": {
                key: round(value / category_total * 100, 2)
                for key, value in counters["categories"].most_common(10)
            },
            "source_share_percent": {
                key: round(value / source_total * 100, 2)
                for key, value in counters["sources"].most_common(10)
            },
        }
    return payload


def snapshot_runtime_metrics() -> Dict[str, Any]:
    with _runtime_lock:
        counters = {scope: dict(counter) for scope, counter in _runtime_counters.items()}
        topup = dict(_topup_state)
    return {
        "counters": counters,
        "topup": topup,
    }


def compute_freshness_metrics(db: Session, *, hours: int = 24) -> Dict[str, Any]:
    """Return freshness observability payload for dashboards and ops."""
    return {
        "as_of": datetime.utcnow().isoformat(),
        "window_hours": hours,
        "runtime": snapshot_runtime_metrics(),
        "interactions_by_freshness_tier": _interaction_metrics_by_tier(db, hours=hours),
        "distribution": _distribution_metrics(db, hours=hours),
    }
