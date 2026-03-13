"""Dedicated YouTube video/reel discovery service."""

from __future__ import annotations

import os
from collections import Counter
from typing import Dict, List

from sqlalchemy.orm import Session

from app.config.video_discovery import (
    DISCOVERY_REGIONS,
    DiscoveryQueryPack,
    discovery_cutoff,
    get_query_packs,
)
from app.core.logging import get_logger
from app.integrations.youtube_client import VideoEntry, YouTubeClient
from app.repositories.video_source_repo import VideoSourceProfileRepository
from app.services.promotion_service import compute_clickbait_penalty
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

        packs = get_query_packs(surface)
        if not packs:
            return []

        pack_limit = min(len(packs), 3)
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

    def _filter_candidates(
        self, candidates: List[VideoEntry], surface: str
    ) -> tuple[List[VideoEntry], Counter]:
        """Apply discovery guardrails and capture rejection counts."""
        accepted: List[VideoEntry] = []
        counters: Counter = Counter()
        seen_ids = set()

        for entry in candidates:
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

            seen_ids.add(entry.video_id)
            accepted.append(entry)

        return accepted, counters

    def _is_english(self, entry: VideoEntry) -> bool:
        lang = (entry.default_language or "").lower()
        return not lang or lang.startswith("en")

    def _is_live_or_scheduled(self, entry: VideoEntry) -> bool:
        status = (entry.live_broadcast_content or "").lower()
        return status in {"live", "upcoming"}
