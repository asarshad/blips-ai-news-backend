"""
Ingestion service.

Orchestrates the ingestion of articles and videos into the content system.
Fetches directly from RSS feeds and YouTube channels with role-based quotas.
"""

import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clustering.dedupe import compute_dedupe_key, compute_title_simhash
from app.clustering.service import ClusteringService
from app.core.config import get_settings
from app.core.curation import review_queue_target_status
from app.core.logging import get_logger
from app.extraction.metrics import extraction_metrics
from app.extraction.pipeline import (
    ExtractionResult,
    RSSEntryData,
    run_extraction,
)
from app.ingestion.extractors import extract_entities, extract_source, extract_topics
from app.ingestion.language_filter import detect_language, is_english
from app.ingestion.url_normalizer import normalize_url
from app.integrations.llm_client import LLMClient
from app.integrations.rss_client import FeedEntry, RSSClient
from app.integrations.rss_feeds import (
    get_quality_modifier as get_rss_quality_modifier,
)
from app.integrations.youtube_channels import (
    get_quality_weight_modifier,
)
from app.integrations.youtube_client import VideoEntry, YouTubeClient
from app.models.content import ContentItem, ContentStatus, ContentType
from app.ranking.quality import compute_source_weight
from app.ranking.service import ScoringService
from app.repositories.content_repo import ContentItemRepository
from app.services.video_content_policy import youtube_discovery_enabled

logger = get_logger(__name__)

settings = get_settings()

# Source fetch depth (larger batches help work around duplicates)
ENTRIES_PER_FEED = settings.RSS_ENTRIES_PER_FEED
VIDEOS_PER_CHANNEL = settings.YT_VIDEOS_PER_CHANNEL

# Daily ingestion targets (per UTC day)
DAILY_TARGET_ARTICLES = settings.DAILY_TARGET_ARTICLES
DAILY_TARGET_VIDEOS = settings.DAILY_TARGET_VIDEOS
DAILY_TARGET_REELS = settings.DAILY_TARGET_REELS

DISCOVERY_FRESH_WINDOW_HOURS = max(1, int(os.getenv("VIDEO_DISCOVERY_FRESH_WINDOW_HOURS", "24")))
DISCOVERY_FRESH_VIDEO_FLOOR = max(
    1,
    int(os.getenv("VIDEO_DISCOVERY_MIN_FRESH_VIDEO_SUPPLY_24H", str(settings.MIN_FRESH_VIDEOS))),
)
DISCOVERY_FRESH_REEL_FLOOR = max(
    1,
    int(os.getenv("VIDEO_DISCOVERY_MIN_FRESH_REEL_SUPPLY_24H", str(settings.MIN_FRESH_REELS))),
)


def _entry_is_reel(entry: VideoEntry) -> bool:
    """Classify a YouTube entry with the same duration-first rule used at ingest time."""
    duration_seconds = getattr(entry, "duration_seconds", None)
    if not isinstance(duration_seconds, (int, float)):
        duration_seconds = None

    is_short = bool(getattr(entry, "is_short", False))
    is_shorts_url = bool(entry.video_url and "/shorts/" in entry.video_url)

    if duration_seconds is not None:
        return duration_seconds <= settings.REEL_MAX_DURATION_SECONDS
    if is_shorts_url:
        return True
    return is_short


class IngestionPipeline:
    """
    Pipeline for ingesting content into the curation system.

    Fetches directly from RSS feeds and YouTube channels,
    extracts metadata, and triggers clustering/scoring.
    """

    def __init__(
        self,
        db: Session,
        content_repo: ContentItemRepository,
        clustering_service: ClusteringService,
        scoring_service: ScoringService,
        rss_client: Optional[RSSClient] = None,
        youtube_client: Optional[YouTubeClient] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        self.db = db
        self.content_repo = content_repo
        self.clustering = clustering_service
        self.scoring = scoring_service
        self.rss_client = rss_client or RSSClient()
        self.youtube_client = youtube_client or YouTubeClient()
        self.llm_client = llm_client or LLMClient()

    def _fresh_promoted_inventory_count(self, content_type: ContentType) -> int:
        """Count recent promoted inventory for the surface users actually see."""
        cutoff = datetime.utcnow() - timedelta(hours=DISCOVERY_FRESH_WINDOW_HOURS)
        count = (
            self.db.query(func.count(ContentItem.id))
            .filter(
                ContentItem.type == content_type,
                ContentItem.curation_status == ContentStatus.PROMOTED,
                ContentItem.is_suppressed.is_(False),
                ContentItem.published_at >= cutoff,
            )
            .scalar()
        )
        return int(count or 0)

    def _discovery_remaining_needed(
        self, content_type: ContentType, *, created_remaining: int
    ) -> int:
        """Let discovery keep running until fresh promoted inventory is healthy."""
        floor = (
            DISCOVERY_FRESH_REEL_FLOOR
            if content_type == ContentType.REEL
            else DISCOVERY_FRESH_VIDEO_FLOOR
        )
        promoted_gap = max(0, floor - self._fresh_promoted_inventory_count(content_type))
        return max(created_remaining, promoted_gap)

    def ingest_rss_entry(self, entry: FeedEntry) -> Optional[ContentItem]:
        """
        Ingest an RSS feed entry directly into content_items.

        Uses enhanced metadata from the new feed configuration system
        for better quality scoring and role-based ranking.

        Args:
            entry: FeedEntry from RSS client (with role metadata)

        Returns:
            Created ContentItem or None if duplicate
        """
        normalized_url = normalize_url(entry.url) if entry.url else entry.url

        # Strong idempotency: source_url is unique in DB.
        if normalized_url:
            existing_by_url = self.content_repo.get_by_source_url(normalized_url)
            if existing_by_url:
                logger.debug(f"Article already ingested (source_url): {entry.title}")
                return None

        source = extract_source(normalized_url or entry.url)
        dedupe_key = compute_dedupe_key(entry.title, source)

        existing = self.content_repo.get_by_dedupe_key(dedupe_key)
        if existing:
            logger.debug(f"Article already ingested: {entry.title}")
            return None

        # Language gate: reject non-English content before spending LLM tokens
        if not is_english(entry.title, entry.content):
            detected_lang, _conf = detect_language(entry.title, entry.content)
            logger.info(
                "[language_filter] Skipping non-English article (lang=%s): %s",
                detected_lang,
                entry.title[:120],
            )
            extraction_metrics.record_language_filtered(detected_lang)
            return None

        # ── Content extraction pipeline ───────────────────────────────────
        extraction: Optional[ExtractionResult] = None
        extraction_enabled = getattr(settings, "EXTRACTION_ENABLED", True)

        if extraction_enabled and normalized_url:
            try:
                rss_data = RSSEntryData(
                    title=entry.title,
                    description=entry.content,
                    image_url=entry.image_url,
                    published_date=entry.published_date,
                )
                extraction = run_extraction(normalized_url, rss_entry=rss_data)

                # Record metrics
                feed_name = getattr(entry, "feed_name", "") or source
                extraction_metrics.record(extraction, source_name=feed_name)

                # Use canonical_url for stronger dedup if available
                if extraction.canonical_url and extraction.canonical_url != normalized_url:
                    existing_canon = self.content_repo.get_by_canonical_url(
                        extraction.canonical_url
                    )
                    if existing_canon:
                        logger.debug(f"Article already ingested (canonical_url): {entry.title}")
                        return None
            except Exception as exc:
                logger.warning(f"Extraction failed for {entry.title}, using RSS data: {exc}")
                extraction = None

        # Determine best content for summarization
        article_text = None
        if extraction and extraction.main_text:
            article_text = extraction.main_text
        elif extraction and extraction.excerpt_fallback:
            article_text = extraction.excerpt_fallback
        elif entry.content:
            article_text = entry.content

        # Determine final image (validated absolute URL or None)
        final_image_url: Optional[str] = None
        if extraction:
            final_image_url = extraction.image_url  # Already validated & absolute or None
        elif entry.image_url:
            from app.extraction.normalize import validate_image_url

            final_image_url = validate_image_url(entry.image_url)

        # Determine canonical_url
        final_canonical_url = None
        if extraction and extraction.canonical_url:
            final_canonical_url = extraction.canonical_url

        # Determine published_at
        final_published_at = entry.published_date or datetime.utcnow()
        if extraction and extraction.published_at:
            final_published_at = extraction.published_at

        # Generate AI summary + conversation starters in one LLM call
        summary = None
        ai_processed = False
        inline_starters = None
        try:
            if self.llm_client.is_configured() and article_text:
                result = self.llm_client.summarize_article(entry.title, article_text)
                summary = result.summary
                inline_starters = result.conversation_starters
                ai_processed = bool(summary and len(summary.strip()) > 50)
        except Exception as e:
            logger.warning(f"Failed to summarize article {entry.title}: {e}")

        topics = extract_topics(
            entry.title, summary or (article_text[:500] if article_text else "")
        )
        entities = extract_entities(entry.title, summary or "")

        final_title = extraction.title if extraction and extraction.title else entry.title
        content_item = ContentItem(
            type=ContentType.ARTICLE,
            source=source,
            source_url=normalized_url or entry.url,
            canonical_url=final_canonical_url,
            published_at=final_published_at,
            title=final_title,
            description=entry.content[:500] if entry.content else None,
            content_text=article_text[:8000] if article_text else None,
            summary=summary,
            image_url=final_image_url,  # Validated: absolute URL or None, never empty string
            video_url=None,
            duration_seconds=None,
            topics=topics,
            entities=entities,
            dedupe_key=dedupe_key,
            simhash=compute_title_simhash(final_title),
            ai_processed=ai_processed,
            conversation_starters=inline_starters,
            language=detected_lang or "en",
        )

        # Apply quality scoring with role-based modifiers
        # Use base_quality_weight from feed config if available, else compute from source
        base_quality = entry.base_quality_weight or compute_source_weight(source)

        # Apply quality tier modifier
        quality_modifier = 1.0
        if entry.quality_tier:
            quality_modifier = get_rss_quality_modifier(entry.quality_tier)

        content_item.quality_score = base_quality * quality_modifier
        content_item.recency_score = 1.0

        self.db.add(content_item)
        try:
            self.db.commit()
            self.db.refresh(content_item)
        except IntegrityError:
            # Common on multi-feed overlaps. Roll back so the session can keep processing.
            self.db.rollback()
            logger.debug(f"Article insert skipped (integrity/duplicate): {entry.title}")
            return None
        except Exception:
            self.db.rollback()
            raise

        self.clustering.cluster_new_item(content_item)
        self._update_scores(content_item)

        # Starters were included in the summary LLM call.
        # Fall back to title-based defaults only if the LLM didn't produce them.
        if not content_item.conversation_starters:
            self._generate_starters_fallback(content_item)

        # Log role info if available
        role_info = ""
        if entry.feed_role:
            role_info = f" [{entry.feed_role.value}]"

        status = "with AI summary" if ai_processed else "without AI summary"
        logger.info(f"Ingested article{role_info} {status}: {entry.title} -> {content_item.id}")
        return content_item

    def ingest_youtube_entry(
        self,
        entry: VideoEntry,
        content_type: ContentType = ContentType.VIDEO,
    ) -> Optional[ContentItem]:
        """
        Ingest a YouTube video entry directly into content_items.

        Uses enhanced metadata from the new channel configuration system
        for better classification and quality scoring.

        Args:
            entry: VideoEntry from YouTube client (with role metadata)
            content_type: VIDEO or REEL (can be overridden by entry.is_short)

        Returns:
            Created ContentItem or None if duplicate
        """
        # Get video duration
        duration_seconds = getattr(entry, "duration_seconds", None)
        if not isinstance(duration_seconds, (int, float)):
            duration_seconds = None
        if duration_seconds is None:
            try:
                duration_seconds = self.youtube_client.get_video_duration(entry.video_id)
            except Exception as e:
                logger.warning(f"Failed to get duration for video {entry.title}: {e}")

        # Classify as REEL if it's a Short.
        # Priority: known duration > URL pattern > entry metadata hint.
        # If we have a concrete duration, that is authoritative.
        if content_type == ContentType.VIDEO:
            is_short = getattr(entry, "is_short", False)
            is_shorts_url = entry.video_url and "/shorts/" in entry.video_url
            is_short_duration = (
                duration_seconds is not None
                and duration_seconds <= settings.REEL_MAX_DURATION_SECONDS
            )
            is_long_duration = (
                duration_seconds is not None
                and duration_seconds > settings.REEL_MAX_DURATION_SECONDS
            )

            if is_long_duration:
                # Authoritative: known duration > 180s → always VIDEO
                content_type = ContentType.VIDEO
            elif is_short_duration:
                # Authoritative: known short duration → always REEL
                content_type = ContentType.REEL
            elif is_shorts_url:
                # URL-based signal (/shorts/ in URL) — strong hint
                content_type = ContentType.REEL
            elif is_short and duration_seconds is None:
                # Metadata hint without duration — accept but log for audit
                content_type = ContentType.REEL
                logger.info(
                    f"Reel classified by metadata hint only (no duration): "
                    f"{entry.title} [{entry.video_id}]"
                )

        source = entry.source or "YouTube"
        # YouTube titles repeat frequently (series/weekly formats). Use video_id for stable dedupe.
        dedupe_key = (
            f"yt:{entry.video_id}" if entry.video_id else compute_dedupe_key(entry.title, source)
        )

        normalized_video_url = (
            normalize_url(entry.video_url) if entry.video_url else entry.video_url
        )

        # Strong idempotency: source_url is unique in DB.
        if normalized_video_url:
            existing_by_url = self.content_repo.get_by_source_url(normalized_video_url)
            if existing_by_url:
                logger.debug(f"Video already ingested (source_url): {entry.title}")
                return None

        existing = self.content_repo.get_by_dedupe_key(dedupe_key)
        if existing:
            logger.debug(f"Video already ingested: {entry.title}")
            return None

        # Language gate: reject non-English videos before spending LLM tokens
        if not is_english(
            entry.title,
            entry.summary,
            channel_language=getattr(entry, "default_language", None),
        ):
            detected_lang, _conf = detect_language(entry.title, entry.summary)
            logger.info(
                "[language_filter] Skipping non-English video (lang=%s): %s",
                detected_lang,
                entry.title[:120],
            )
            extraction_metrics.record_language_filtered(detected_lang)
            return None

        # Generate AI summary + conversation starters (skip for reels - metadata only)
        summary = entry.summary
        ai_processed = False
        inline_starters = None

        # Skip AI summarization for REEL content type
        if content_type == ContentType.REEL:
            # Reels use metadata only, no summarization
            ai_processed = False
        else:
            try:
                if self.llm_client.is_configured() and summary:
                    # Check if summary is generic
                    is_generic = (
                        "Watch this video" in summary
                        or "Subscribe" in summary.lower()
                        or len(summary.strip()) < 50
                    )
                    if is_generic:
                        # Try to get transcript
                        transcript = self.youtube_client.get_transcript(entry.video_id)
                        if transcript:
                            summary = transcript[:5000]

                    video_result = self.llm_client.summarize_video(entry.title, summary)
                    ai_summary = video_result.summary
                    inline_starters = video_result.conversation_starters
                    if ai_summary and len(ai_summary.strip()) > 50:
                        summary = ai_summary
                        ai_processed = True
            except Exception as e:
                logger.warning(f"Failed to summarize video {entry.title}: {e}")

        # Apply quality tier modifier from channel config
        channel_quality_tier = getattr(entry, "quality_tier", None)
        quality_modifier = 1.0
        if channel_quality_tier:
            quality_modifier = get_quality_weight_modifier(channel_quality_tier)

        topics = extract_topics(entry.title, summary or "")
        entities = extract_entities(entry.title, summary or "")

        content_item = ContentItem(
            type=content_type,
            source=source,
            source_url=normalized_video_url or entry.video_url,
            channel_id=getattr(entry, "channel_id", None),
            # Discovery and curated candidates now carry their true publish time.
            published_at=entry.published_at or datetime.utcnow(),
            title=entry.title,
            description=summary[:500] if summary else None,
            summary=summary if content_type != ContentType.REEL else None,
            image_url=entry.thumbnail_url,
            video_url=normalized_video_url or entry.video_url,
            duration_seconds=duration_seconds,
            topics=topics,
            entities=entities,
            dedupe_key=dedupe_key,
            simhash=compute_title_simhash(entry.title),
            ai_processed=ai_processed,
            conversation_starters=inline_starters,
            language=detected_lang or "en",
            curation_status=review_queue_target_status(),
            discovered_via=f"yt_{getattr(entry, 'acquisition_lane', 'curated')}",
            acquisition_lane=getattr(entry, "acquisition_lane", "curated"),
            source_status=getattr(entry, "source_status", None),
            view_count_snapshot=getattr(entry, "view_count", None),
            engagement_snapshot={
                "likes": getattr(entry, "like_count", None),
                "comments": getattr(entry, "comment_count", None),
            },
            views_per_hour=getattr(entry, "views_per_hour", None),
            format_fit_score=getattr(entry, "format_fit_score", None),
        )
        base_quality = compute_source_weight(source)
        content_item.quality_score = base_quality * quality_modifier
        content_item.recency_score = 1.0

        self.db.add(content_item)
        try:
            self.db.commit()
            self.db.refresh(content_item)
        except IntegrityError:
            self.db.rollback()
            logger.debug(f"Video insert skipped (integrity/duplicate): {entry.title}")
            return None
        except Exception:
            self.db.rollback()
            raise

        self.clustering.cluster_new_item(content_item)
        self._update_scores(content_item)

        # Starters were included in the summary LLM call.
        # Fall back to title-based defaults only if the LLM didn't produce them.
        if content_type != ContentType.REEL and not content_item.conversation_starters:
            self._generate_starters_fallback(content_item)

        # Log role info if available
        role_info = ""
        channel_role = getattr(entry, "channel_role", None)
        if channel_role:
            role_info = f" [{channel_role.value}]"

        status = "with AI summary" if ai_processed else "without AI summary"
        logger.info(
            f"Ingested {content_type.value}{role_info} {status}: {entry.title} -> {content_item.id}"
        )
        return content_item

    def run_backfill(
        self,
        hours_back: int = 72,
        limit: int = 500,
        *,
        ingest_until_targets: bool = False,
        max_catchup_attempts: int = 5,
        max_catchup_seconds: int = 600,
        catchup_sleep_seconds: float = 5.0,
    ) -> Dict[str, int]:
        """Fetch and ingest content from RSS feeds and YouTube channels.

        This enforces per-day quotas (UTC day) based on items created today.

        By default, this runs a single pass. When `ingest_until_targets=True`, it will
        keep attempting (with gradually deeper source fetches) until daily targets are met
        or a time/attempt budget is reached.

        Args:
            hours_back: Not used (kept for API compatibility)
            limit: Not used (kept for API compatibility)
            ingest_until_targets: Keep retrying until daily targets are met
            max_catchup_attempts: Maximum number of attempts in a single call
            max_catchup_seconds: Time budget for catch-up loop
            catchup_sleep_seconds: Sleep between attempts

        Returns:
            Statistics about the ingestion
        """

        # Allow environment override without changing all call sites.
        env_ingest_until = os.getenv("INGEST_UNTIL_TARGETS")
        if env_ingest_until is not None:
            ingest_until_targets = env_ingest_until.lower() in ("true", "1", "yes", "on")

        start_time = time.monotonic()
        attempts = 0

        aggregate = {
            "articles_processed": 0,
            "articles_ingested": 0,
            "articles_skipped_duplicates": 0,
            "videos_processed": 0,
            "videos_ingested": 0,
            "videos_skipped_duplicates": 0,
            "reels_processed": 0,
            "reels_ingested": 0,
            "reels_skipped_duplicates": 0,
            "errors": 0,
            "attempts": 0,
            "daily_targets_met": False,
        }

        from app.services.video_source_service import bootstrap_video_source_profiles

        bootstrap_video_source_profiles(self.db)

        while True:
            # Enforce per-day quotas based on items created today (UTC).
            # Articles retain daily caps; videos/reels are continuous (no daily stop).
            today = datetime.utcnow().date()
            existing_articles = self.content_repo.count_created_on_date(ContentType.ARTICLE, today)
            existing_videos = self.content_repo.count_created_on_date(ContentType.VIDEO, today)
            existing_reels = self.content_repo.count_created_on_date(ContentType.REEL, today)

            remaining_articles = max(0, DAILY_TARGET_ARTICLES - existing_articles)

            if remaining_articles == 0:
                logger.info(
                    "Article daily target met (UTC %s): articles=%s, videos=%s, reels=%s",
                    today,
                    existing_articles,
                    existing_videos,
                    existing_reels,
                )
                aggregate["daily_targets_met"] = True
                return aggregate

            # If we aren't in catch-up mode, we only do one attempt.
            if attempts > 0 and not ingest_until_targets:
                aggregate["attempts"] = attempts
                return aggregate

            if attempts >= max_catchup_attempts:
                logger.warning(
                    "Catch-up attempt limit reached (%s) with remaining article target: %s",
                    max_catchup_attempts,
                    remaining_articles,
                )
                aggregate["attempts"] = attempts
                return aggregate

            elapsed = time.monotonic() - start_time
            if elapsed >= max_catchup_seconds:
                logger.warning(
                    "Catch-up time budget reached (%ss) with remaining article target: %s",
                    int(max_catchup_seconds),
                    remaining_articles,
                )
                aggregate["attempts"] = attempts
                return aggregate

            attempt_num = attempts + 1
            # Fetch deeper on later attempts to work around duplicates.
            entries_per_feed = min(int(ENTRIES_PER_FEED * attempt_num), 100)
            videos_per_channel = min(int(VIDEOS_PER_CHANNEL * attempt_num), 50)

            logger.info(
                "Ingestion attempt %s (UTC %s): remaining articles=%s existing videos=%s reels=%s (fetch depth: rss=%s/channel=%s)",
                attempt_num,
                today,
                remaining_articles,
                existing_videos,
                existing_reels,
                entries_per_feed,
                videos_per_channel,
            )

            stats = {
                "articles_processed": 0,
                "articles_ingested": 0,
                "articles_skipped_duplicates": 0,
                "videos_processed": 0,
                "videos_ingested": 0,
                "videos_skipped_duplicates": 0,
                "reels_processed": 0,
                "reels_ingested": 0,
                "reels_skipped_duplicates": 0,
                "errors": 0,
            }

            # Fetch articles and videos in parallel
            logger.info("Fetching content from RSS feeds and YouTube channels in parallel...")

            feed_entries: List[FeedEntry] = []
            video_entries: List[VideoEntry] = []

            with ThreadPoolExecutor(max_workers=2) as executor:
                # Submit both fetch tasks
                article_future = executor.submit(
                    self.rss_client.fetch_all_feeds,
                    entries_per_feed=entries_per_feed,
                )
                video_future = executor.submit(
                    self.youtube_client.fetch_all_channels,
                    videos_per_channel=videos_per_channel,
                )

                # Collect results - RSS fetching can take a while due to content extraction
                try:
                    feed_entries = article_future.result(timeout=300)  # 5 minutes for RSS
                    logger.info(f"Fetched {len(feed_entries)} RSS entries")
                except TimeoutError:
                    logger.error("RSS feed fetching timed out after 300 seconds")
                    stats["errors"] += 1
                except Exception as e:
                    logger.error(f"Error fetching RSS feeds: {type(e).__name__}: {e}")
                    stats["errors"] += 1

                try:
                    video_entries = video_future.result(timeout=180)  # 3 minutes for YouTube
                    logger.info(f"Fetched {len(video_entries)} YouTube videos")
                except TimeoutError:
                    logger.error("YouTube channel fetching timed out after 180 seconds")
                    stats["errors"] += 1
                except Exception as e:
                    logger.error(f"Error fetching YouTube channels: {type(e).__name__}: {e}")
                    stats["errors"] += 1

            if youtube_discovery_enabled():
                # Dedicated YouTube discovery lane for broader coverage and recency.
                try:
                    from app.services.video_discovery_service import VideoDiscoveryService

                    discovery = VideoDiscoveryService(self.db, self.youtube_client)
                    discovered_videos = discovery.discover("videos", remaining_needed=None)
                    discovered_reels = discovery.discover("reels", remaining_needed=None)
                    video_entries.extend(discovered_videos)
                    video_entries.extend(discovered_reels)
                    logger.info(
                        "Fetched %s discovery candidates (%s videos, %s reels)",
                        len(discovered_videos) + len(discovered_reels),
                        len(discovered_videos),
                        len(discovered_reels),
                    )
                except Exception as e:
                    logger.error(
                        f"Error fetching YouTube discovery candidates: {type(e).__name__}: {e}"
                    )
                    stats["errors"] += 1

            # Process articles until we hit the remaining daily target
            # Track per-feed counts to enforce daily caps
            feed_article_counts: Dict[str, int] = defaultdict(int)

            logger.info(
                "Processing articles for UTC %s: existing=%s target=%s remaining=%s",
                today,
                existing_articles,
                DAILY_TARGET_ARTICLES,
                remaining_articles,
            )
            for entry in feed_entries:
                if stats["articles_ingested"] >= remaining_articles:
                    break

                # Apply per-feed daily caps
                feed_name = getattr(entry, "feed_name", "") or "unknown"
                feed_daily_cap = 3  # Default cap
                if hasattr(entry, "quality_tier") and entry.quality_tier:
                    # Premium feeds get slightly higher effective caps
                    feed_daily_cap = 4 if entry.quality_tier.value == "premium" else 3

                if feed_article_counts[feed_name] >= feed_daily_cap:
                    logger.debug(f"Feed {feed_name} hit daily cap ({feed_daily_cap}), skipping")
                    continue

                stats["articles_processed"] += 1
                try:
                    result = self.ingest_rss_entry(entry)
                    if result:
                        stats["articles_ingested"] += 1
                        feed_article_counts[feed_name] += 1
                    else:
                        stats["articles_skipped_duplicates"] += 1
                except Exception as e:
                    logger.error(f"Error ingesting article {entry.title}: {e}")
                    self.db.rollback()
                    stats["errors"] += 1

            # Log feed distribution
            if feed_article_counts:
                logger.info("Articles ingested by feed:")
                for feed, count in sorted(feed_article_counts.items(), key=lambda x: -x[1]):
                    logger.info(f"  {feed}: {count}")

            # Process YouTube entries (no daily cap; per-channel caps still enforced).
            # Track channels to prevent excessive repetition
            channel_video_counts: Dict[str, int] = defaultdict(int)
            channel_reel_counts: Dict[str, int] = defaultdict(int)

            logger.info(
                "Processing YouTube for UTC %s: existing videos=%s reels=%s",
                today,
                existing_videos,
                existing_reels,
            )
            video_entries.sort(
                key=lambda entry: (
                    getattr(entry, "format_fit_score", 0.0) or 0.0,
                    getattr(entry, "views_per_hour", 0.0) or 0.0,
                    entry.published_at or datetime.min,
                ),
                reverse=True,
            )
            for entry in video_entries:
                # Use enhanced metadata for shorts detection
                is_reel = _entry_is_reel(entry)

                # Apply per-channel caps to prevent creator fatigue
                channel_id = getattr(entry, "channel_id", "") or entry.source
                if is_reel:
                    if channel_reel_counts[channel_id] >= 4:  # Max 4 reels per channel
                        continue
                else:
                    if channel_video_counts[channel_id] >= 2:  # Max 2 videos per channel
                        continue

                if is_reel:
                    stats["reels_processed"] += 1
                else:
                    stats["videos_processed"] += 1
                try:
                    result = self.ingest_youtube_entry(entry)
                    if not result:
                        # Count duplicate skip into the best-effort bucket
                        if is_reel:
                            stats["reels_skipped_duplicates"] += 1
                        else:
                            stats["videos_skipped_duplicates"] += 1
                        continue

                    # Update channel counts
                    if result.type == ContentType.REEL:
                        stats["reels_ingested"] += 1
                        channel_reel_counts[channel_id] += 1
                    else:
                        stats["videos_ingested"] += 1
                        channel_video_counts[channel_id] += 1
                except Exception as e:
                    logger.error(f"Error ingesting video {entry.title}: {e}")
                    self.db.rollback()
                    stats["errors"] += 1

            for key, value in stats.items():
                aggregate[key] += value

            attempts += 1
            aggregate["attempts"] = attempts
            logger.info(f"Ingestion attempt {attempt_num} complete: {stats}")

            # If we didn't ingest anything new, don't spin forever.
            if (
                stats["articles_ingested"] + stats["videos_ingested"] + stats["reels_ingested"]
            ) == 0:
                logger.warning(
                    "No new items ingested in attempt %s; stopping early (remaining targets may not be reachable with current sources)",
                    attempt_num,
                )
                return aggregate

            if ingest_until_targets and catchup_sleep_seconds > 0:
                time.sleep(catchup_sleep_seconds)

    def ingest_video_discovery_candidates(self) -> Dict[str, int | str]:
        """Fetch and persist YouTube search/trending candidates in the live worker path."""
        if not youtube_discovery_enabled():
            return {
                "status": "disabled",
                "videos_candidates": 0,
                "videos_ingested": 0,
                "reels_candidates": 0,
                "reels_ingested": 0,
                "duplicates": 0,
                "errors": 0,
            }

        # Use promoted inventory gaps to determine discovery batch size.
        # Videos/reels are no longer daily-capped; discovery runs every cycle.
        promoted_gap_videos = max(
            0,
            DISCOVERY_FRESH_VIDEO_FLOOR - self._fresh_promoted_inventory_count(ContentType.VIDEO),
        )
        promoted_gap_reels = max(
            0,
            DISCOVERY_FRESH_REEL_FLOOR - self._fresh_promoted_inventory_count(ContentType.REEL),
        )
        # Always discover at least a baseline batch even when inventory is healthy
        remaining_videos = max(promoted_gap_videos, 10)
        remaining_reels = max(promoted_gap_reels, 10)

        logger.info(
            "[video_discovery] remaining_needed videos=%s (promoted_gap=%s) reels=%s (promoted_gap=%s)",
            remaining_videos,
            promoted_gap_videos,
            remaining_reels,
            promoted_gap_reels,
        )

        from app.services.video_discovery_service import VideoDiscoveryService
        from app.services.video_source_service import bootstrap_video_source_profiles

        bootstrap_video_source_profiles(self.db)
        discovery = VideoDiscoveryService(self.db, self.youtube_client)
        channel_video_counts: Dict[str, int] = defaultdict(int)
        channel_reel_counts: Dict[str, int] = defaultdict(int)
        result: Dict[str, int | str] = {
            "status": "ok",
            "videos_candidates": 0,
            "videos_ingested": 0,
            "reels_candidates": 0,
            "reels_ingested": 0,
            "duplicates": 0,
            "errors": 0,
        }

        for surface in ("videos", "reels"):
            try:
                entries = discovery.discover(
                    surface,
                    remaining_needed=remaining_videos if surface == "videos" else remaining_reels,
                )
            except Exception as exc:
                logger.error("[video_discovery] %s discovery failed: %s", surface, exc)
                result["errors"] = int(result["errors"]) + 1
                continue

            result[f"{surface}_candidates"] = len(entries)
            entries.sort(
                key=lambda entry: (
                    1 if str(getattr(entry, "query_label", "") or "").startswith("story-") else 0,
                    getattr(entry, "format_fit_score", 0.0) or 0.0,
                    getattr(entry, "views_per_hour", 0.0) or 0.0,
                    entry.published_at or datetime.min,
                ),
                reverse=True,
            )

            for entry in entries:
                channel_id = getattr(entry, "channel_id", "") or entry.source or "unknown"
                if surface == "videos":
                    if channel_video_counts[channel_id] >= 2:
                        continue
                else:
                    if channel_reel_counts[channel_id] >= 4:
                        continue

                try:
                    item = self.ingest_youtube_entry(entry)
                except Exception as exc:
                    logger.error(
                        "[video_discovery] Failed to ingest %s candidate '%s': %s",
                        surface,
                        entry.title,
                        exc,
                    )
                    self.db.rollback()
                    result["errors"] = int(result["errors"]) + 1
                    continue

                if not item:
                    result["duplicates"] = int(result["duplicates"]) + 1
                    continue

                if item.type == ContentType.REEL:
                    channel_reel_counts[channel_id] += 1
                    result["reels_ingested"] = int(result["reels_ingested"]) + 1
                else:
                    channel_video_counts[channel_id] += 1
                    result["videos_ingested"] = int(result["videos_ingested"]) + 1

        if int(result["videos_ingested"]) == 0 and int(result["reels_ingested"]) == 0:
            result["status"] = "no_new_items"

        return result

    def _update_scores(self, content_item: ContentItem) -> None:
        """Update scores for a newly ingested item."""
        scores = self.scoring.score_single_item(content_item)
        self.content_repo.update_scores(
            content_item.id,
            quality_score=scores["quality"],
            trend_score=scores["trend"],
            recency_score=scores["recency"],
            diversity_boost=scores["diversity"],
            global_score=scores["global"],
        )

    def _generate_starters_fallback(self, content_item: ContentItem) -> None:
        """Persist title-based default starters when the summary LLM call didn't produce them."""
        try:
            from app.services.conversation_starters import ConversationStartersService

            defaults = ConversationStartersService()._get_default_starters(content_item)
            content_item.conversation_starters = defaults
            self.db.commit()
            logger.info(f"Persisted fallback starters for content_id={content_item.id}")
        except Exception as e:
            logger.warning(f"Failed to persist fallback starters for {content_item.id}: {e}")
            self.db.rollback()


def create_ingestion_pipeline(db: Session) -> IngestionPipeline:
    """Factory function to create IngestionPipeline with dependencies."""
    from app.clustering.service import ClusteringService
    from app.ranking.service import ScoringService
    from app.repositories.content_repo import ContentItemRepository
    from app.repositories.user_repo import InteractionEventRepository

    content_repo = ContentItemRepository(db)
    event_repo = InteractionEventRepository(db)

    clustering = ClusteringService(content_repo)
    scoring = ScoringService(content_repo, event_repo)

    return IngestionPipeline(
        db=db,
        content_repo=content_repo,
        clustering_service=clustering,
        scoring_service=scoring,
    )


def run_video_discovery_ingestion(db: Session) -> Dict[str, int | str]:
    """Run the YouTube discovery lane inside the live scheduler and top-up paths."""
    pipeline = create_ingestion_pipeline(db)
    return pipeline.ingest_video_discovery_candidates()
