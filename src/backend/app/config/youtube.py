"""
YouTube channel sources configuration.

Defines all YouTube channels to fetch for video ingestion.
"""

from typing import List, Dict
from pydantic_settings import BaseSettings
from pydantic import Field


class YouTubeChannel:
    """Represents a single YouTube channel source."""
    
    def __init__(
        self,
        name: str,
        channel_id: str,
        category: str = "Tech",
        quality_weight: float = 0.5,
        is_shorts_channel: bool = False,
        enabled: bool = True
    ):
        self.name = name
        self.channel_id = channel_id
        self.category = category
        self.quality_weight = quality_weight
        self.is_shorts_channel = is_shorts_channel
        self.enabled = enabled


# Premium tech reviewers
PREMIUM_CHANNELS: List[YouTubeChannel] = [
    YouTubeChannel(
        name="MKBHD",
        channel_id="UCBJycsmduvYEL83R_U4JriQ",
        quality_weight=0.90,
    ),
    YouTubeChannel(
        name="Dave2D",
        channel_id="UCVYamHliCI9rw1tHR1xbkfw",
        quality_weight=0.85,
    ),
    YouTubeChannel(
        name="Linus Tech Tips",
        channel_id="UCXuqSBlHAE6Xw-yeJA0Tunw",
        quality_weight=0.80,
    ),
]

# General tech channels
GENERAL_CHANNELS: List[YouTubeChannel] = [
    YouTubeChannel(
        name="Austin Evans",
        channel_id="UCXGgrKt94gR6lmN4aN3mYTg",
        quality_weight=0.75,
    ),
    YouTubeChannel(
        name="Unbox Therapy",
        channel_id="UCsTcErHg8oDvUnTzoqsYeNw",
        quality_weight=0.70,
    ),
    YouTubeChannel(
        name="The Verge",
        channel_id="UCddiUEpeqJcYeBxX1IVBKvQ",
        quality_weight=0.85,
    ),
]

# AI-focused channels
AI_CHANNELS: List[YouTubeChannel] = [
    YouTubeChannel(
        name="Two Minute Papers",
        channel_id="UCbfYPyITQ-7l4upoX8nvctg",
        category="AI",
        quality_weight=0.85,
    ),
    YouTubeChannel(
        name="AI Explained",
        channel_id="UCNF0LEQ2abMr0PAX3cfkAMg",
        category="AI",
        quality_weight=0.80,
    ),
]

# Shorts channels (for Reels)
# Note: YouTube Shorts are served from the same channel RSS feeds as regular videos.
# Videos under 60 seconds are automatically classified as REEL type during ingestion.
# These channels typically produce more short-form content.
SHORTS_CHANNELS: List[YouTubeChannel] = [
    YouTubeChannel(
        name="MKBHD",
        channel_id="UCBJycsmduvYEL83R_U4JriQ",
        is_shorts_channel=True,
        quality_weight=0.85,
    ),
    YouTubeChannel(
        name="Linus Tech Tips",
        channel_id="UCXuqSBlHAE6Xw-yeJA0Tunw",
        is_shorts_channel=True,
        quality_weight=0.80,
    ),
    YouTubeChannel(
        name="The Verge",
        channel_id="UCddiUEpeqJcYeBxX1IVBKvQ",
        is_shorts_channel=True,
        quality_weight=0.85,
    ),
]


def get_all_channels() -> List[YouTubeChannel]:
    """Get all enabled YouTube channels including shorts-capable channels."""
    all_channels = PREMIUM_CHANNELS + GENERAL_CHANNELS + AI_CHANNELS + SHORTS_CHANNELS
    # Deduplicate by channel_id (some channels may appear in multiple lists)
    seen = set()
    unique = []
    for c in all_channels:
        if c.enabled and c.channel_id not in seen:
            seen.add(c.channel_id)
            unique.append(c)
    return unique


def get_shorts_channels() -> List[YouTubeChannel]:
    """Get channels configured for Shorts/Reels."""
    return [c for c in get_all_channels() if c.is_shorts_channel]


def get_channel_ids() -> List[str]:
    """Get list of all enabled channel IDs."""
    return [c.channel_id for c in get_all_channels()]


def get_channels_by_category(category: str) -> List[YouTubeChannel]:
    """Get channels filtered by category."""
    return [c for c in get_all_channels() if c.category == category]
