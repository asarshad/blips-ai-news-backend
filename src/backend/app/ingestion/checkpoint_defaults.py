"""Checkpointed ingestion defaults.

Isolates deterministic "what should we ingest" config from the runner.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Dict, List

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class FeedDefault:
    source_type: str
    feed_name: str
    target: int


def owner_token() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"


def parse_target_overrides() -> Dict[str, int]:
    """Parse INGESTION_TARGET_DEFAULTS.

    Format: JSON object mapping "{source_type}:{feed_name}" -> int.
    """

    raw = os.getenv("INGESTION_TARGET_DEFAULTS", "").strip()
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except Exception:
        logger.warning("Invalid INGESTION_TARGET_DEFAULTS JSON; ignoring")
        return {}

    result: Dict[str, int] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                result[str(k)] = int(v)
            except Exception:
                continue
    return result


def build_defaults() -> List[FeedDefault]:
    """Build per-feed daily targets from configured RSS feeds + YouTube channels."""

    from app.integrations.rss_client import RSSClient
    from app.integrations.youtube_channels import ContentFormat
    from app.integrations.youtube_client import YouTubeClient

    overrides = parse_target_overrides()

    defaults: List[FeedDefault] = []

    # RSS: 1 row per feed
    rss_client = RSSClient()
    for cfg in rss_client.feed_configs:
        key = f"rss:{cfg.name}"
        target = int(overrides.get(key, cfg.daily_cap))
        defaults.append(FeedDefault("rss", cfg.name, max(0, target)))

    # YouTube: split per channel into video vs reel targets based on format
    yt_client = YouTubeClient()
    for cfg in yt_client.channel_configs:
        if cfg.content_format == ContentFormat.LONG_FORM:
            key = f"youtube_video:{cfg.name}"
            target = int(overrides.get(key, cfg.daily_cap))
            defaults.append(FeedDefault("youtube_video", cfg.name, max(0, target)))
        elif cfg.content_format == ContentFormat.SHORTS:
            key = f"youtube_reel:{cfg.name}"
            target = int(overrides.get(key, cfg.daily_cap))
            defaults.append(FeedDefault("youtube_reel", cfg.name, max(0, target)))
        else:
            # MIXED: split daily_cap across video and reels
            video_target = max(1, int(cfg.daily_cap) // 2)
            reel_target = max(0, int(cfg.daily_cap) - video_target)

            key_v = f"youtube_video:{cfg.name}"
            key_r = f"youtube_reel:{cfg.name}"
            video_target = int(overrides.get(key_v, video_target))
            reel_target = int(overrides.get(key_r, reel_target))

            defaults.append(FeedDefault("youtube_video", cfg.name, max(0, video_target)))
            if reel_target > 0:
                defaults.append(FeedDefault("youtube_reel", cfg.name, max(0, reel_target)))

    return [d for d in defaults if d.target > 0]
