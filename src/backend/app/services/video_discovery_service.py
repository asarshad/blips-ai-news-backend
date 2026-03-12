"""Dedicated YouTube video/reel discovery service."""

from __future__ import annotations

from collections import Counter
from typing import Dict, List

from sqlalchemy.orm import Session

from app.config.video_discovery import DISCOVERY_REGIONS, discovery_cutoff, iter_lane_matrix
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


class VideoDiscoveryService:
    """Searches and filters discovery candidates for videos and reels."""

    def __init__(self, db: Session, youtube_client: YouTubeClient | None = None) -> None:
        self.db = db
        self.youtube_client = youtube_client or YouTubeClient()
        self.repo = VideoSourceProfileRepository(db)

    def discover(self, surface: str) -> List[VideoEntry]:
        """Return unique hydrated candidates for the requested surface."""
        bootstrap_video_source_profiles(self.db)

        unique: Dict[str, VideoEntry] = {}
        cutoff = discovery_cutoff(surface)

        for pack, region in iter_lane_matrix(surface):
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

        for region in DISCOVERY_REGIONS:
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
