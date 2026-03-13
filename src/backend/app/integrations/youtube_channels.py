"""
YouTube channel configuration for curated video/reel ingestion.

The registry is stored as data so we can grow the trusted roster without
turning this module into a giant hand-maintained Python list.
"""

from __future__ import annotations

import json
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


@dataclass(frozen=True)
class ChannelConfig:
    """Configuration for a single curated YouTube channel."""

    channel_id: str
    name: str
    role: ChannelRole
    content_format: ContentFormat = ContentFormat.LONG_FORM
    daily_cap: int = 1
    quality_tier: QualityTier = QualityTier.STANDARD
    enabled: bool = True
    notes: str = ""

    @property
    def feed_url(self) -> str:
        """Generate the YouTube RSS feed URL from the channel ID."""
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={self.channel_id}"


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
                quality_tier=QualityTier(entry.get("quality_tier", "standard")),
                enabled=bool(entry.get("enabled", True)),
                notes=str(entry.get("notes", "")),
            )
        )
    return configs


CHANNEL_REGISTRY: List[ChannelConfig] = _load_channel_registry()


def _channel_config_priority(config: ChannelConfig) -> tuple[int, int, int]:
    """Prefer enabled, non-shorts, mixed-capable configs for duplicate IDs."""
    format_rank = {
        ContentFormat.MIXED: 2,
        ContentFormat.LONG_FORM: 1,
        ContentFormat.SHORTS: 0,
    }[config.content_format]
    role_rank = 0 if config.role == ChannelRole.SHORTS else 1
    enabled_rank = 1 if config.enabled else 0
    return (enabled_rank, role_rank, format_rank)


def dedupe_channel_configs(configs: Iterable[ChannelConfig]) -> List[ChannelConfig]:
    """Collapse duplicate channel IDs down to a single canonical config."""
    unique: Dict[str, ChannelConfig] = {}
    for config in configs:
        current = unique.get(config.channel_id)
        if current is None or _channel_config_priority(config) > _channel_config_priority(current):
            unique[config.channel_id] = config
    return list(unique.values())


def get_enabled_channels() -> List[ChannelConfig]:
    """Get the enabled curated channels."""
    return [channel for channel in dedupe_channel_configs(CHANNEL_REGISTRY) if channel.enabled]


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
        if channel.content_format in (ContentFormat.SHORTS, ContentFormat.MIXED)
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
    """Look up a channel by case-insensitive partial name match."""
    name_lower = name.lower()
    for channel in dedupe_channel_configs(CHANNEL_REGISTRY):
        if name_lower in channel.name.lower():
            return channel
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
    role_counts = {role.value: len(get_channels_by_role(role)) for role in ChannelRole}
    total_daily_cap = sum(channel.daily_cap for channel in enabled)
    long_form_cap = sum(channel.daily_cap for channel in get_long_form_channels())
    shorts_cap = sum(channel.daily_cap for channel in get_shorts_channels())
    return {
        "total_channels": len(enabled),
        "total_daily_cap": total_daily_cap,
        "long_form_daily_cap": long_form_cap,
        "shorts_daily_cap": shorts_cap,
        "channels_by_role": role_counts,
        "premium_channels": len(
            [channel for channel in enabled if channel.quality_tier == QualityTier.PREMIUM]
        ),
    }
