"""Dedicated YouTube video/reel discovery service."""

from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy.orm import Session

from app.config.video_discovery import (
    DISCOVERY_REGIONS,
    DiscoveryQueryPack,
    discovery_cutoff,
    get_query_packs,
)
from app.core.logging import get_logger
from app.ingestion.language_filter import detect_language, is_non_english
from app.integrations.youtube_channels import ChannelRole
from app.integrations.youtube_client import VideoEntry, YouTubeClient
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.video_source import VideoSourceProfile
from app.repositories.video_source_repo import VideoSourceProfileRepository
from app.services.promotion_service import compute_clickbait_penalty, compute_story_keyword_signal
from app.services.video_source_service import bootstrap_video_source_profiles

_ALLOWED_CATEGORIES = {
    "Artificial Intelligence",
    "Technology",
    "Mobile",
    "Hardware",
    "Software",
    "Reviews",
    "Cloud & Infrastructure",
    "Cybersecurity",
    "Startups & Business",
    "AI Tools",
    "Developer & Engineering",
}

logger = get_logger(__name__)
_DISCOVERY_REGION_PRIORITY = tuple(
    dict.fromkeys(os.getenv("YOUTUBE_DISCOVERY_REGION_PRIORITY", "US,GB,CA,IN").split(","))
)
_SKIP_STORY_ENTITIES = {
    "technology",
    "tech",
    "news",
    "video",
    "videos",
    "review",
    "update",
    "shorts",
}
_DISCOVERY_MIN_CHANNEL_SUBSCRIBERS = max(
    1, int(os.getenv("YOUTUBE_DISCOVERY_MIN_CHANNEL_SUBSCRIBERS", "10000"))
)
_DISCOVERY_MIN_CHANNEL_VIDEOS = max(1, int(os.getenv("YOUTUBE_DISCOVERY_MIN_CHANNEL_VIDEOS", "25")))
_DISCOVERY_MIN_VIDEO_VIEWS = max(1, int(os.getenv("YOUTUBE_DISCOVERY_MIN_VIDEO_VIEWS", "2500")))
_DISCOVERY_MIN_VIEWS_PER_HOUR = max(
    1.0, float(os.getenv("YOUTUBE_DISCOVERY_MIN_VIEWS_PER_HOUR", "75"))
)
_DISCOVERY_MIN_ENGAGEMENT_RATE = max(
    0.0, float(os.getenv("YOUTUBE_DISCOVERY_MIN_ENGAGEMENT_RATE", "0.003"))
)
_DISCOVERY_STORY_MIN_SUBSCRIBERS = max(
    1, int(os.getenv("YOUTUBE_DISCOVERY_STORY_MIN_SUBSCRIBERS", "5000"))
)
_DISCOVERY_MIN_INITIAL_VIDEO_VIEWS = max(
    1, int(os.getenv("YOUTUBE_DISCOVERY_MIN_INITIAL_VIDEO_VIEWS", "250"))
)
_DISCOVERY_MIN_INITIAL_VIEWS_PER_HOUR = max(
    1.0, float(os.getenv("YOUTUBE_DISCOVERY_MIN_INITIAL_VIEWS_PER_HOUR", "10"))
)
_DISCOVERY_HIGH_AUTHORITY_SUBSCRIBERS = max(
    _DISCOVERY_MIN_CHANNEL_SUBSCRIBERS,
    int(os.getenv("YOUTUBE_DISCOVERY_HIGH_AUTHORITY_SUBSCRIBERS", "100000")),
)
_TECH_CHANNEL_HINTS = (
    "tech",
    "ai",
    "apple",
    "google",
    "android",
    "pixel",
    "iphone",
    "mac",
    "code",
    "coding",
    "dev",
    "developer",
    "program",
    "byte",
    "gadget",
    "review",
    "benchmark",
    "hardware",
    "software",
    "linux",
    "cloud",
    "cyber",
    "privacy",
    "security",
    "openai",
    "anthropic",
    "gemini",
    "claude",
    "copilot",
    "silicon",
    "startup",
    "venture",
)
_ENTRY_TECH_KEYWORDS = (
    "ai",
    "android",
    "apple",
    "app",
    "api",
    "camera",
    "chatgpt",
    "chip",
    "claude",
    "cloud",
    "code",
    "copilot",
    "developer",
    "device",
    "galaxy",
    "gemini",
    "github",
    "google",
    "gpu",
    "hardware",
    "iphone",
    "linux",
    "mac",
    "openai",
    "phone",
    "pixel",
    "privacy",
    "python",
    "review",
    "security",
    "software",
    "update",
)
_ENTRY_OFF_TOPIC_KEYWORDS = (
    "challenge",
    "family",
    "giveaway",
    "manager",
    "podcast",
    "prank",
    "reaction",
    "skate park",
    "splashdown",
    "travel",
    "viral",
    "vlog",
)


class VideoDiscoveryService:
    """Searches and filters discovery candidates for videos and reels."""

    def __init__(self, db: Session, youtube_client: YouTubeClient | None = None) -> None:
        self.db = db
        self.youtube_client = youtube_client or YouTubeClient()
        self.repo = VideoSourceProfileRepository(db)

    def discover(self, surface: str, *, remaining_needed: int | None = None) -> List[VideoEntry]:
        """Return unique hydrated candidates for the requested surface."""
        if remaining_needed is not None and remaining_needed <= 0:
            return []

        bootstrap_video_source_profiles(self.db)

        unique: Dict[str, VideoEntry] = {}
        cutoff = discovery_cutoff(surface)

        search_plan = self._build_search_plan(surface, remaining_needed)
        if search_plan:
            if self.youtube_client.quota_budget.begin_search_window(surface):
                for pack, region in search_plan:
                    candidates = self.youtube_client.fetch_search_candidates(
                        pack.query,
                        region_code=region,
                        max_results=pack.max_results,
                        surface=surface,
                        search_order=pack.order,
                        query_label=pack.label,
                        published_after=cutoff,
                    )
                    accepted, counters = self._filter_candidates(candidates, surface)
                    self.repo.record_run(
                        lane="search",
                        surface=surface,
                        query_label=pack.label,
                        region=region,
                        candidate_count=len(candidates),
                        duplicate_rejections=counters["duplicate"],
                        clickbait_rejections=counters["clickbait"],
                        filtered_non_english=counters["non_english"],
                        filtered_live=counters["live"],
                        filtered_off_topic=counters["off_topic"],
                        filtered_format=counters["format"],
                    )
                    for entry in accepted:
                        unique.setdefault(entry.video_id, entry)
            else:
                logger.info(
                    "[video_discovery] Skipping %s search: cooldown active or quota lockout",
                    surface,
                )

        if self._trending_enabled(surface):
            for region in self._select_trending_regions(remaining_needed):
                candidates = self.youtube_client.fetch_trending_candidates(
                    region_code=region,
                    max_results=10 if surface == "reels" else 20,
                    surface=surface,
                )
                accepted, counters = self._filter_candidates(candidates, surface)
                self.repo.record_run(
                    lane="trending",
                    surface=surface,
                    query_label="most-popular-tech",
                    region=region,
                    candidate_count=len(candidates),
                    duplicate_rejections=counters["duplicate"],
                    clickbait_rejections=counters["clickbait"],
                    filtered_non_english=counters["non_english"],
                    filtered_live=counters["live"],
                    filtered_off_topic=counters["off_topic"],
                    filtered_format=counters["format"],
                )
                for entry in accepted:
                    unique.setdefault(entry.video_id, entry)

        self.db.commit()
        return list(unique.values())

    def _build_search_plan(
        self, surface: str, remaining_needed: int | None
    ) -> List[tuple[DiscoveryQueryPack, str]]:
        """Choose a small set of high-value search calls based on the current deficit."""
        deficit = remaining_needed if remaining_needed is not None else 999
        min_deficit = self._min_search_deficit(surface)
        if deficit < min_deficit:
            return []

        packs = self._prioritized_query_packs(surface)
        if not packs:
            return []

        pack_limit = min(len(packs), 4)
        region_limit = min(
            len(self._ordered_regions()), 2 if deficit >= (18 if surface == "videos" else 12) else 1
        )
        max_calls = self._max_search_calls(surface, deficit)

        selected_packs = packs[:pack_limit]
        selected_regions = self._ordered_regions()[:region_limit]
        plan: List[tuple[DiscoveryQueryPack, str]] = []
        for call_index in range(max_calls):
            pack = selected_packs[call_index % len(selected_packs)]
            region = selected_regions[(call_index // len(selected_packs)) % len(selected_regions)]
            plan.append((pack, region))
        return plan

    def _prioritized_query_packs(self, surface: str) -> List[DiscoveryQueryPack]:
        packs: List[DiscoveryQueryPack] = []
        seen_queries: set[str] = set()

        for pack in self._build_story_query_packs(surface) + get_query_packs(surface):
            normalized = pack.query.strip().lower()
            if not normalized or normalized in seen_queries:
                continue
            packs.append(pack)
            seen_queries.add(normalized)

        return packs

    def _build_story_query_packs(self, surface: str) -> List[DiscoveryQueryPack]:
        if not hasattr(self.db, "query"):
            return []

        cutoff = datetime.utcnow() - timedelta(
            days=max(1, int(os.getenv("YOUTUBE_DISCOVERY_STORY_WINDOW_DAYS", "7")))
        )
        items = (
            self.db.query(ContentItem)
            .filter(
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.is_suppressed.is_(False),
                ContentItem.published_at >= cutoff,
            )
            .order_by(ContentItem.published_at.desc())
            .limit(250)
            .all()
        )
        if not items:
            return []

        topic_counts: Counter = Counter()
        entity_counts: Counter = Counter()
        for item in items:
            weight = 2 if item.type == ContentType.ARTICLE else 1
            for topic in self._normalize_terms(item.topics)[:2]:
                topic_counts[topic] += weight
            for entity in self._normalize_terms(item.entities)[:3]:
                entity_counts[entity] += weight

        dynamic_packs: List[DiscoveryQueryPack] = []
        for entity, count in entity_counts.most_common(4):
            if count < 2 or entity in _SKIP_STORY_ENTITIES:
                continue
            category = self._infer_story_category(entity, topic_counts)
            query = self._build_story_query(surface, entity, category)
            if not query:
                continue
            dynamic_packs.append(
                DiscoveryQueryPack(
                    label=f"story-{entity.replace(' ', '-')[:32]}",
                    query=query,
                    category=category,
                    surface=surface,
                    max_results=25,
                    order="relevance",
                )
            )
            if len(dynamic_packs) >= 2:
                break

        return dynamic_packs

    def _normalize_terms(self, values: object) -> List[str]:
        if not isinstance(values, list):
            return []

        normalized: List[str] = []
        seen: set[str] = set()
        for value in values:
            if isinstance(value, str):
                term = value.strip().lower()
            elif isinstance(value, dict):
                term = str(value.get("name") or value.get("value") or "").strip().lower()
            else:
                continue
            if not term or term in seen:
                continue
            normalized.append(term)
            seen.add(term)
        return normalized

    def _infer_story_category(self, entity: str, topic_counts: Counter) -> str:
        text = entity.lower()
        if any(
            token in text for token in ("iphone", "pixel", "galaxy", "macbook", "ipad", "laptop")
        ):
            return "mobile/hardware"
        if any(
            token in text for token in ("openai", "chatgpt", "gpt", "claude", "gemini", "copilot")
        ):
            return "ai"
        if any(
            token in text
            for token in ("linux", "python", "react", "typescript", "docker", "kubernetes")
        ):
            return "engineer/dev"
        if any(token in text for token in ("privacy", "security", "vpn", "breach", "ransomware")):
            return "security/privacy"

        if topic_counts:
            topic, _count = topic_counts.most_common(1)[0]
            if topic in {
                "ai",
                "mobile/hardware",
                "engineer/dev",
                "security/privacy",
                "business/industry",
            }:
                return topic
        return "news"

    def _build_story_query(self, surface: str, entity: str, category: str) -> str:
        if surface == "reels":
            suffix_by_category = {
                "ai": "update shorts",
                "mobile/hardware": "hands on shorts",
                "engineer/dev": "quick take shorts",
                "security/privacy": "update shorts",
                "business/industry": "quick take shorts",
                "news": "update shorts",
            }
        else:
            suffix_by_category = {
                "ai": "update",
                "mobile/hardware": "hands on review",
                "engineer/dev": "release overview",
                "security/privacy": "security update",
                "business/industry": "analysis",
                "news": "launch update",
            }

        suffix = suffix_by_category.get(category, suffix_by_category["news"])
        return f"{entity} {suffix}".strip()

    def _max_search_calls(self, surface: str, deficit: int) -> int:
        if surface == "reels":
            if deficit >= 16:
                return 3
            if deficit >= 9:
                return 2
            return 1
        if deficit >= 21:
            return 3
        if deficit >= 11:
            return 2
        return 1

    def _min_search_deficit(self, surface: str) -> int:
        env_name = (
            "YOUTUBE_SEARCH_MIN_REEL_DEFICIT"
            if surface == "reels"
            else "YOUTUBE_SEARCH_MIN_VIDEO_DEFICIT"
        )
        default = "4" if surface == "reels" else "6"
        return max(1, int(os.getenv(env_name, default)))

    def _ordered_regions(self) -> List[str]:
        configured = [
            region.strip().upper() for region in _DISCOVERY_REGION_PRIORITY if region.strip()
        ]
        allowed = {region.upper() for region in DISCOVERY_REGIONS}
        ordered = [region for region in configured if region in allowed]
        if len(ordered) == len(allowed):
            return ordered
        return ordered + [
            region for region in DISCOVERY_REGIONS if region.upper() not in set(ordered)
        ]

    def _select_trending_regions(self, remaining_needed: int | None) -> List[str]:
        ordered = self._ordered_regions()
        if remaining_needed is None:
            return ordered
        if remaining_needed >= 12:
            return ordered
        if remaining_needed >= 6:
            return ordered[:2]
        return ordered[:1]

    def _trending_enabled(self, surface: str) -> bool:
        env_name = (
            "YOUTUBE_REEL_TRENDING_ENABLED"
            if surface == "reels"
            else "YOUTUBE_VIDEO_TRENDING_ENABLED"
        )
        default = "0" if surface == "reels" else "1"
        return os.getenv(env_name, default).strip().lower() not in {"0", "false", "no", "off"}

    def _filter_candidates(
        self, candidates: List[VideoEntry], surface: str
    ) -> tuple[List[VideoEntry], Counter]:
        """Apply discovery guardrails and capture rejection counts."""
        accepted: List[VideoEntry] = []
        counters: Counter = Counter()
        seen_ids = set()
        channel_stats = self.youtube_client.fetch_channel_stats(
            [entry.channel_id for entry in candidates if entry.channel_id]
        )
        profiles = self.repo.get_many(
            [entry.channel_id for entry in candidates if entry.channel_id]
        )

        for entry in candidates:
            stats = channel_stats.get(entry.channel_id or "", {})
            profile = profiles.get(entry.channel_id or "")
            entry.channel_subscriber_count = self._stat_as_int(stats.get("subscriber_count"))
            entry.channel_video_count = self._stat_as_int(stats.get("video_count"))
            if not entry.video_id or entry.video_id in seen_ids:
                counters["duplicate"] += 1
                continue
            if self._is_live_or_scheduled(entry):
                counters["live"] += 1
                continue
            if not self._is_english(entry):
                counters["non_english"] += 1
                continue
            if entry.category not in _ALLOWED_CATEGORIES:
                counters["off_topic"] += 1
                continue
            if compute_clickbait_penalty(entry.title) >= 0.4:
                counters["clickbait"] += 1
                continue
            if not entry.format_fit_score or entry.format_fit_score <= 0:
                counters["format"] += 1
                continue
            if surface == "reels" and not entry.is_short:
                counters["format"] += 1
                continue
            if surface == "videos" and entry.is_short:
                counters["format"] += 1
                continue
            if not self._profile_allows_entry(profile, entry=entry, surface=surface):
                counters["quality"] += 1
                continue
            if not self._passes_discovery_quality(entry, surface=surface, profile=profile):
                counters["quality"] += 1
                continue

            seen_ids.add(entry.video_id)
            accepted.append(entry)

        self.repo.upsert_discovered_channels(
            {
                "channel_id": entry.channel_id,
                "channel_name": entry.source,
                "role": entry.channel_role.value if entry.channel_role else "explainer",
                "content_format": entry.content_format.value if entry.content_format else "mixed",
                "quality_tier": entry.quality_tier.value if entry.quality_tier else "standard",
            }
            for entry in accepted
            if entry.source_status == "discovery" and entry.channel_id
        )
        return accepted, counters

    def _is_english(self, entry: VideoEntry) -> bool:
        lang = (entry.default_language or "").lower()
        if lang:
            return lang.startswith("en")

        detected_lang, _confidence = detect_language(entry.title, entry.summary)
        return not is_non_english(detected_lang)

    def _is_live_or_scheduled(self, entry: VideoEntry) -> bool:
        status = (entry.live_broadcast_content or "").lower()
        return status in {"live", "upcoming"}

    def _profile_allows_entry(
        self,
        profile: VideoSourceProfile | None,
        *,
        entry: VideoEntry,
        surface: str,
    ) -> bool:
        if profile is None:
            return True
        if not profile.enabled or profile.status == "blocked":
            return False

        lane = (entry.acquisition_lane or "").strip().lower()
        if lane == "search" and not profile.allow_search:
            return False
        if lane == "trending" and not profile.allow_trending:
            return False
        if surface == "reels" and int(profile.daily_reel_cap or 0) <= 0:
            return False
        return True

    def _passes_discovery_quality(
        self,
        entry: VideoEntry,
        *,
        surface: str,
        profile: VideoSourceProfile | None = None,
    ) -> bool:
        if entry.acquisition_lane == "curated" or entry.source_status in {"core", "rotation"}:
            return True

        subscribers = entry.channel_subscriber_count or 0
        channel_videos = entry.channel_video_count or 0
        view_count = entry.view_count or 0
        views_per_hour = entry.views_per_hour or 0.0
        likes = entry.like_count or 0
        comments = entry.comment_count or 0
        engagement_rate = 0.0
        if view_count >= _DISCOVERY_MIN_INITIAL_VIDEO_VIEWS:
            engagement_rate = (likes + comments * 2) / view_count
        strong_channel = (
            subscribers >= _DISCOVERY_MIN_CHANNEL_SUBSCRIBERS
            and channel_videos >= _DISCOVERY_MIN_CHANNEL_VIDEOS
        )
        strong_video = (
            view_count >= _DISCOVERY_MIN_VIDEO_VIEWS
            or views_per_hour >= _DISCOVERY_MIN_VIEWS_PER_HOUR
        )
        strong_engagement = engagement_rate >= _DISCOVERY_MIN_ENGAGEMENT_RATE
        basic_traction = (
            view_count >= _DISCOVERY_MIN_INITIAL_VIDEO_VIEWS
            or views_per_hour >= _DISCOVERY_MIN_INITIAL_VIEWS_PER_HOUR
        )
        story_led = (
            isinstance(entry.query_label, str)
            and entry.query_label.startswith("story-")
            and subscribers >= _DISCOVERY_STORY_MIN_SUBSCRIBERS
        )
        story_signal = compute_story_keyword_signal(entry.title, entry.summary or "")
        tech_signal = self._entry_tech_signal(entry)
        trusted_channel = (
            entry.channel_role == ChannelRole.OFFICIAL
            or self._channel_name_looks_tech(entry.source)
            or subscribers >= _DISCOVERY_HIGH_AUTHORITY_SUBSCRIBERS
        )

        if surface == "reels":
            if not trusted_channel:
                return False
            if tech_signal < 0.35 and story_signal < 0.18:
                return False
            if entry.channel_role == ChannelRole.OFFICIAL:
                return tech_signal >= 0.40 or (
                    story_led and (basic_traction or strong_engagement) and story_signal >= 0.18
                )
            if strong_channel and strong_video and story_signal >= 0.18 and tech_signal >= 0.30:
                return True
            if strong_video and strong_engagement and (story_signal >= 0.18 or tech_signal >= 0.52):
                return True
            if story_led and trusted_channel and (basic_traction or strong_engagement):
                return tech_signal >= 0.30
            return False

        if strong_channel and trusted_channel and strong_video:
            return True
        if (
            entry.acquisition_lane == "trending"
            and trusted_channel
            and (strong_video or basic_traction)
            and (story_signal >= 0.18 or tech_signal >= 0.30)
        ):
            return True
        if story_led and trusted_channel and (basic_traction or strong_engagement):
            return True
        if strong_video and strong_engagement and trusted_channel:
            return True
        return False

    def _channel_name_looks_tech(self, source: str | None) -> bool:
        if not isinstance(source, str):
            return False
        text = source.strip().lower()
        if not text:
            return False
        return any(token in text for token in _TECH_CHANNEL_HINTS)

    def _entry_tech_signal(self, entry: VideoEntry) -> float:
        text = " ".join([entry.title or "", entry.summary or "", entry.source or ""]).lower()
        if not text:
            return 0.0

        keyword_hits = sum(1 for token in _ENTRY_TECH_KEYWORDS if token in text)
        off_topic_hits = sum(1 for token in _ENTRY_OFF_TOPIC_KEYWORDS if token in text)

        if entry.channel_role in {ChannelRole.AI, ChannelRole.ENGINEER}:
            base = 0.20
        elif entry.channel_role == ChannelRole.OFFICIAL:
            base = 0.18
        elif entry.channel_role == ChannelRole.NEWS:
            base = 0.12
        else:
            base = 0.08

        score = base + min(keyword_hits, 6) * 0.10 - min(off_topic_hits, 3) * 0.18
        return round(min(max(score, 0.0), 1.0), 4)

    def _stat_as_int(self, value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
