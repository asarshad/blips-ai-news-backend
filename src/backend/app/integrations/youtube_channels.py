"""
YouTube channel configuration for curated video/reel ingestion.

The registry is stored as data so we can grow the trusted roster without
turning this module into a giant hand-maintained Python list.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional


class ChannelRole(str, Enum):
    """Content role classification for channels."""

    EXPLAINER = "explainer"
    NEWS = "news"
    ENGINEER = "engineer"
    AI = "ai"
    OFFICIAL = "official"
    SHORTS = "shorts"


class ContentFormat(str, Enum):
    """Content format classification."""

    LONG_FORM = "long_form"
    SHORTS = "shorts"
    MIXED = "mixed"


class QualityTier(str, Enum):
    """Quality tier for ranking weight adjustment."""

    PREMIUM = "premium"
    STANDARD = "standard"
    SUPPLEMENTAL = "supplemental"


class IngestionStream(str, Enum):
    """Stream grouping for primary vs fill-only curated channels."""

    PRIMARY = "primary"
    EXPANSION = "expansion"


@dataclass(frozen=True)
class ChannelConfig:
    """Configuration for a single curated YouTube channel."""

    channel_id: str
    name: str
    role: ChannelRole
    content_format: ContentFormat = ContentFormat.LONG_FORM
    daily_cap: int = 1
    daily_reel_cap: Optional[int] = None
    allow_reels: Optional[bool] = None
    allow_search: bool = True
    allow_trending: bool = True
    fresh_published_hours: Optional[int] = None
    backfill_created_hours: Optional[int] = None
    evergreen_max_days: Optional[int] = None
    bootstrap_on_add: bool = False
    quality_tier: QualityTier = QualityTier.STANDARD
    ingestion_stream: IngestionStream = IngestionStream.PRIMARY
    enabled: bool = True
    notes: str = ""

    @property
    def feed_url(self) -> str:
        """Generate the YouTube RSS feed URL from the channel ID."""
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={self.channel_id}"

    @property
    def shorts_feed_url(self) -> str:
        """YouTube Shorts-only RSS feed via the UUSH playlist prefix.

        YouTube exposes a hidden playlist for each channel's Shorts that uses
        the UUSH prefix instead of the standard UC prefix.  Entries from this
        feed are definitively Shorts, so callers can skip duration / permalink
        heuristics.
        """
        if not self.channel_id.startswith("UC"):
            return self.feed_url
        playlist_id = "UUSH" + self.channel_id[2:]
        return f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"

    @property
    def reels_enabled(self) -> bool:
        """Whether this channel should contribute to the reels surface."""
        if self.allow_reels is not None:
            return self.allow_reels
        return self.content_format in (ContentFormat.SHORTS, ContentFormat.MIXED)

    @property
    def effective_daily_reel_cap(self) -> int:
        """Resolved reel cap after applying defaults and explicit overrides."""
        if not self.reels_enabled:
            return 0
        if self.daily_reel_cap is not None:
            return max(0, int(self.daily_reel_cap))
        if self.content_format == ContentFormat.LONG_FORM:
            return 1
        return self.daily_cap

    @property
    def has_age_overrides(self) -> bool:
        """Whether this channel uses custom feed age windows."""
        return any(
            value is not None
            for value in (
                self.fresh_published_hours,
                self.backfill_created_hours,
                self.evergreen_max_days,
            )
        )


_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "youtube_curated_channels.json"


def _load_channel_registry() -> List[ChannelConfig]:
    """Load the curated roster from disk."""
    raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    configs: List[ChannelConfig] = []
    for entry in raw:
        configs.append(
            ChannelConfig(
                channel_id=str(entry["channel_id"]).strip(),
                name=str(entry["name"]).strip(),
                role=ChannelRole(entry["role"]),
                content_format=ContentFormat(entry.get("content_format", "long_form")),
                daily_cap=max(1, int(entry.get("daily_cap", 1))),
                daily_reel_cap=(
                    max(0, int(entry["daily_reel_cap"]))
                    if entry.get("daily_reel_cap") is not None
                    else None
                ),
                allow_reels=(
                    bool(entry["allow_reels"])
                    if "allow_reels" in entry and entry.get("allow_reels") is not None
                    else None
                ),
                allow_search=bool(entry.get("allow_search", True)),
                allow_trending=bool(entry.get("allow_trending", True)),
                fresh_published_hours=(
                    max(1, int(entry["fresh_published_hours"]))
                    if entry.get("fresh_published_hours") is not None
                    else None
                ),
                backfill_created_hours=(
                    max(0, int(entry["backfill_created_hours"]))
                    if entry.get("backfill_created_hours") is not None
                    else None
                ),
                evergreen_max_days=(
                    max(1, int(entry["evergreen_max_days"]))
                    if entry.get("evergreen_max_days") is not None
                    else None
                ),
                bootstrap_on_add=bool(entry.get("bootstrap_on_add", False)),
                quality_tier=QualityTier(entry.get("quality_tier", "standard")),
                ingestion_stream=IngestionStream(entry.get("ingestion_stream", "primary")),
                enabled=bool(entry.get("enabled", True)),
                notes=str(entry.get("notes", "")),
            )
        )
    return configs


CHANNEL_REGISTRY: List[ChannelConfig] = _load_channel_registry()


def _normalize_channel_name(name: str) -> str:
    """Normalize channel names for resilient source matching."""
    if not name:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def _channel_config_priority(config: ChannelConfig) -> tuple[int, int, int, int]:
    """Prefer enabled, non-shorts, mixed-capable configs for duplicate IDs."""
    format_rank = {
        ContentFormat.MIXED: 2,
        ContentFormat.LONG_FORM: 1,
        ContentFormat.SHORTS: 0,
    }[config.content_format]
    role_rank = 0 if config.role == ChannelRole.SHORTS else 1
    enabled_rank = 1 if config.enabled else 0
    stream_rank = 1 if config.ingestion_stream == IngestionStream.PRIMARY else 0
    return (enabled_rank, stream_rank, role_rank, format_rank)


def dedupe_channel_configs(configs: Iterable[ChannelConfig]) -> List[ChannelConfig]:
    """Collapse duplicate channel IDs down to a single canonical config."""
    unique: Dict[str, ChannelConfig] = {}
    for config in configs:
        current = unique.get(config.channel_id)
        if current is None or _channel_config_priority(config) > _channel_config_priority(current):
            unique[config.channel_id] = config
    return list(unique.values())


def get_enabled_channels(
    *, ingestion_stream: Optional[IngestionStream] = None
) -> List[ChannelConfig]:
    """Get enabled curated channels, optionally filtered by stream."""
    enabled = [channel for channel in dedupe_channel_configs(CHANNEL_REGISTRY) if channel.enabled]
    if ingestion_stream is None:
        return enabled
    return [channel for channel in enabled if channel.ingestion_stream == ingestion_stream]


def get_primary_channels() -> List[ChannelConfig]:
    """Get enabled always-on curated channels."""
    return get_enabled_channels(ingestion_stream=IngestionStream.PRIMARY)


def get_expansion_channels() -> List[ChannelConfig]:
    """Get enabled fill-only curated channels."""
    return get_enabled_channels(ingestion_stream=IngestionStream.EXPANSION)


def get_bootstrap_channels() -> List[ChannelConfig]:
    """Get curated channels that should receive a one-time bootstrap pass."""
    return [channel for channel in get_enabled_channels() if channel.bootstrap_on_add]


def get_channels_by_role(role: ChannelRole) -> List[ChannelConfig]:
    """Get enabled channels for a specific role."""
    return [channel for channel in get_enabled_channels() if channel.role == role]


def get_long_form_channels() -> List[ChannelConfig]:
    """Get channels that can contribute long-form videos."""
    return [
        channel
        for channel in get_enabled_channels()
        if channel.content_format in (ContentFormat.LONG_FORM, ContentFormat.MIXED)
    ]


def get_shorts_channels() -> List[ChannelConfig]:
    """Get channels that can contribute reels/shorts."""
    return [
        channel
        for channel in get_enabled_channels()
        if channel.reels_enabled and channel.effective_daily_reel_cap > 0
    ]


def get_channel_feed_urls() -> List[str]:
    """Get RSS feed URLs for all enabled channels."""
    return [channel.feed_url for channel in get_enabled_channels()]


def get_channel_by_id(channel_id: str) -> Optional[ChannelConfig]:
    """Look up a channel by ID."""
    for channel in dedupe_channel_configs(CHANNEL_REGISTRY):
        if channel.channel_id == channel_id:
            return channel
    return None


def get_channel_by_name(name: str) -> Optional[ChannelConfig]:
    """Look up a channel by normalized exact or near-exact name match."""
    normalized_name = _normalize_channel_name(name)
    if not normalized_name:
        return None

    best_match: Optional[ChannelConfig] = None
    best_score = -1
    best_name_length = 10**9

    for channel in dedupe_channel_configs(CHANNEL_REGISTRY):
        normalized_channel = _normalize_channel_name(channel.name)
        if not normalized_channel:
            continue

        score = -1
        if normalized_name == normalized_channel:
            score = 3
        elif normalized_name in normalized_channel or normalized_channel in normalized_name:
            score = 2
        if score > best_score or (
            score == best_score and score >= 0 and len(normalized_channel) < best_name_length
        ):
            best_match = channel
            best_score = score
            best_name_length = len(normalized_channel)

    if best_score >= 0:
        return best_match
    return None


def get_role_quotas() -> Dict[ChannelRole, Dict[str, int]]:
    """Return heuristic per-role mix targets for admin diagnostics."""
    return {
        ChannelRole.EXPLAINER: {"min": 12, "target": 24, "max": 40},
        ChannelRole.NEWS: {"min": 10, "target": 18, "max": 30},
        ChannelRole.ENGINEER: {"min": 8, "target": 16, "max": 28},
        ChannelRole.AI: {"min": 4, "target": 8, "max": 14},
        ChannelRole.OFFICIAL: {"min": 8, "target": 18, "max": 32},
        ChannelRole.SHORTS: {"min": 12, "target": 20, "max": 32},
    }


def get_quality_weight_modifier(tier: QualityTier) -> float:
    """Return the ranking weight modifier for the quality tier."""
    return {
        QualityTier.PREMIUM: 1.15,
        QualityTier.STANDARD: 1.0,
        QualityTier.SUPPLEMENTAL: 0.85,
    }[tier]


def get_channel_stats() -> Dict[str, object]:
    """Summarise the current curated channel registry."""
    enabled = get_enabled_channels()
    primary = get_primary_channels()
    expansion = get_expansion_channels()
    role_counts = {role.value: len(get_channels_by_role(role)) for role in ChannelRole}
    total_daily_cap = sum(channel.daily_cap for channel in enabled)
    long_form_cap = sum(channel.daily_cap for channel in get_long_form_channels())
    shorts_cap = sum(channel.effective_daily_reel_cap for channel in get_shorts_channels())
    return {
        "total_channels": len(enabled),
        "primary_channels": len(primary),
        "expansion_channels": len(expansion),
        "total_daily_cap": total_daily_cap,
        "long_form_daily_cap": long_form_cap,
        "shorts_daily_cap": shorts_cap,
        "channels_by_role": role_counts,
        "premium_channels": len(
            [channel for channel in enabled if channel.quality_tier == QualityTier.PREMIUM]
        ),
    }
