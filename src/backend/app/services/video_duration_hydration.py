"""Best-effort hydration of missing video durations at serve time."""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from app.core.logging import get_logger
from app.ingestion.canonical import extract_youtube_video_id
from app.integrations.youtube_client import YouTubeClient
from app.models.content import ContentType
from app.repositories.content_repo import ContentItemRepository
from app.video_surface_rules import effective_content_type

logger = get_logger(__name__)

MAX_DURATION_HYDRATION_LOOKUPS = 8


def hydrate_missing_video_durations(
    items: Iterable,
    *,
    content_repo: ContentItemRepository,
    youtube_client: Optional[YouTubeClient] = None,
    max_lookups: int = MAX_DURATION_HYDRATION_LOOKUPS,
) -> Dict[int, int]:
    """Fill and persist missing YouTube durations for a bounded number of items."""
    if max_lookups <= 0:
        return {}

    hydrated: Dict[int, int] = {}
    attempted_video_ids: set[str] = set()
    lookups = 0
    client = youtube_client or YouTubeClient()

    for item in items:
        if lookups >= max_lookups:
            break

        if getattr(item, "id", None) in hydrated:
            continue

        if getattr(item, "duration_seconds", None) is not None:
            continue

        item_type = effective_content_type(item)
        if item_type not in (ContentType.VIDEO, ContentType.REEL):
            continue

        video_id = extract_youtube_video_id(getattr(item, "video_url", "") or "") or (
            extract_youtube_video_id(getattr(item, "source_url", "") or "")
        )
        if not video_id or video_id in attempted_video_ids:
            continue

        attempted_video_ids.add(video_id)
        lookups += 1

        try:
            duration_seconds = client.get_video_duration(video_id)
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.warning("Duration hydration failed for %s: %s", video_id, exc)
            continue

        if not isinstance(duration_seconds, int) or duration_seconds <= 0:
            continue

        hydrated[item.id] = duration_seconds

    if not hydrated:
        return {}

    try:
        content_repo.update_duration_seconds_bulk(hydrated)
    except Exception as exc:  # pragma: no cover - defensive logging path
        logger.warning("Failed to persist hydrated durations: %s", exc)
        content_repo.db.rollback()
        return {}

    return hydrated
