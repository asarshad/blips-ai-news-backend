"""Signal ingestion orchestrator.

This module is the core "Coverage Guarantee" engine.  It:

1. Fetches candidate URLs from each signal source (HN, GitHub, YT Trending).
2. Canonicalizes every URL.
3. Upserts each into ``signal_urls`` (bumps hit_count on repeat sightings).
4. Cross-checks against ``content_items``:
   a. If the URL already exists  → mark as DUPLICATE, increment signal_hits.
   b. If the URL is new          → create a minimal review-queue stub so the
      promotion + extraction pipeline can take over.

Review-queue stubs have:
  - ``curation_status = CANDIDATE`` by default, or ``PROMOTED`` when
    ``AUTO_APPROVE_REVIEW_CONTENT=true``
  - ``ai_processed = False``          (LLM skipped until promoted)
  - ``discovered_via`` set to the signal source label

Normally only after the PromotionService promotes them to PROMOTED do items
become eligible for AI summarisation and feed display.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.article_hydration import ArticleHydrationService
from app.config.source_tiering import get_domain_policy, is_allowed_domain
from app.core.curation import review_queue_target_status
from app.ingestion.canonical import canonical_key_for_article, extract_youtube_video_id
from app.ingestion.signals import SignalItem
from app.ingestion.signals.discovery_feeds import fetch_discovery_leads
from app.ingestion.signals.github_trending import fetch_github_trending
from app.ingestion.signals.hacker_news import fetch_hn_best, fetch_hn_top
from app.ingestion.signals.youtube_trending import fetch_yt_trending
from app.ingestion.url_normalizer import normalize_url
from app.models.candidate_audit import CandidateAuditEvent
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.signal import SignalSource
from app.repositories.content_repo import ContentItemRepository
from app.repositories.signal_repo import SignalURLRepository

logger = logging.getLogger(__name__)


# ── Discovery-via labels ──────────────────────────────────────────────────────

_SIGNAL_SOURCE_LABELS: Dict[SignalSource, str] = {
    SignalSource.HN_TOP: "signal_hn",
    SignalSource.HN_BEST: "signal_hn",
    SignalSource.GITHUB_TRENDING: "signal_github",
    SignalSource.YT_TRENDING: "signal_yt_trending",
    SignalSource.DISCOVERY_LEADS: "signal_discovery",
}

# Max article/video stub candidates created per orchestrator run
_MAX_STUBS_PER_RUN = 30


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class SignalIngestionResult:
    """Summary of a single signal orchestrator run."""

    signal_urls_seen: int = 0
    signal_urls_added: int = 0  # New signal_urls rows inserted
    signal_hits_bumped: int = 0  # Existing content items that got signal_hits++
    stubs_created: int = 0  # New CANDIDATE content stubs
    stubs_skipped: int = 0  # Skipped (integrity error / content type unknown)
    domain_rejected: int = 0  # Rejected by domain tiering policy
    errors: List[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _detect_content_type(url: str) -> ContentType:
    """Heuristically decide whether a URL is article or video content."""
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return ContentType.VIDEO
    return ContentType.ARTICLE


def _find_existing(
    content_repo: ContentItemRepository,
    canonical_url: str,
) -> Optional[ContentItem]:
    """Multi-probe lookup for a URL in content_items."""
    # 1. Direct source URL match
    existing = content_repo.get_by_source_url(canonical_url)
    if existing:
        return existing

    # 2. Canonical URL match (after extraction pipeline may have resolved redirects)
    existing = content_repo.get_by_canonical_url(canonical_url)
    if existing:
        return existing

    # 3. YouTube video-ID match
    yt_vid = extract_youtube_video_id(canonical_url)
    if yt_vid:
        existing = content_repo.get_by_canonical_key(yt_vid)
        if existing:
            return existing
        # YouTube URLs store canonical_key as the video ID, not a sha256 hash;
        # skip probe 4 to avoid a wasted query.
        return None

    # 4. Hash-based canonical_key (article URLs only)
    ckey = canonical_key_for_article(canonical_url=canonical_url, source_url=canonical_url)
    if ckey:
        existing = content_repo.get_by_canonical_key(ckey)
        if existing:
            return existing

    return None


def _build_candidate_stub(
    url: str,
    item: SignalItem,
    content_type: ContentType,
) -> ContentItem:
    """Construct a minimal CANDIDATE ContentItem stub for a new signal URL.

    The stub is deliberately thin – only the fields needed for the
    promotion scorer and extraction pipeline are populated.
    """
    from app.ingestion.canonical import canonical_key_for_article, canonical_key_for_youtube
    from app.ingestion.extractors import extract_source, extract_topics

    source = extract_source(url)
    topics: list = extract_topics(item.raw_title or "", "") if item.raw_title else []
    domain_policy = get_domain_policy(url)

    if content_type == ContentType.VIDEO:
        yt_vid = extract_youtube_video_id(url)
        ckey = canonical_key_for_youtube(video_id=yt_vid, source_url=url, video_url=url)
    else:
        ckey = canonical_key_for_article(canonical_url=url, source_url=url)

    discovered_via = _SIGNAL_SOURCE_LABELS.get(item.signal_source, "signal")

    stub = ContentItem(
        type=content_type,
        source=source,
        source_url=url,
        canonical_url=url,
        canonical_key=ckey,
        published_at=datetime.utcnow(),
        ingestion_day=date.today(),
        title=(item.raw_title or url)[:1000],
        candidate_first_seen_at=datetime.utcnow(),
        candidate_signal_source=item.signal_source.value,
        candidate_raw_title=(item.raw_title or url)[:1000],
        description=None,
        content_text=None,
        summary=None,
        image_url=None,
        video_url=url if content_type == ContentType.VIDEO else None,
        topics=topics,
        entities=[],
        dedupe_key=None,
        ai_processed=False,
        language="en",
        # Seed with policy tier weight so promotion has better priors.
        quality_score=domain_policy.quality_weight,
        recency_score=1.0,
        trend_score=0.0,
        global_score=0.0,
        curation_status=review_queue_target_status(),
        discovered_via=discovered_via,
        signal_hits=1,
    )
    return stub


def _record_candidate_audit_event(
    db: Session,
    *,
    canonical_url: str,
    signal_source: SignalSource,
    event_type: str,
    discovered_via: Optional[str] = None,
    content_item_id: Optional[int] = None,
    reason: Optional[str] = None,
    payload: Optional[dict] = None,
) -> None:
    db.add(
        CandidateAuditEvent(
            canonical_url=canonical_url,
            signal_source=signal_source.value,
            discovered_via=discovered_via,
            content_item_id=content_item_id,
            event_type=event_type,
            reason=reason,
            payload=payload,
        )
    )


# ── Main entry-point ──────────────────────────────────────────────────────────


def run_signal_ingestion(
    db: Session,
    *,
    yt_api_key: Optional[str] = None,
    hn_limit: int = 50,
    github_limit: int = 25,
    yt_limit: int = 30,
    discovery_limit: int = 25,
    discovery_per_source_limit: int = 5,
    discovery_enabled: bool = True,
    max_stubs: int = _MAX_STUBS_PER_RUN,
) -> SignalIngestionResult:
    """Fetch all signal sources and cross-check / enqueue new URLs.

    Args:
        db:          SQLAlchemy session (caller owns commit/rollback).
        yt_api_key:  YouTube Data API v3 key (optional).
        hn_limit:    Max items to fetch from each HN endpoint.
        github_limit: Max repos from GitHub Trending.
        yt_limit:    Max videos from YouTube Trending.
        discovery_limit: Max links from discovery feed fetcher.
        discovery_per_source_limit: Max links per discovery feed source.
        discovery_enabled: Include discovery feed fetcher in this run.
        max_stubs:   Cap on new CANDIDATE stubs created per run.

    Returns:
        SignalIngestionResult with run statistics.
    """
    result = SignalIngestionResult()
    content_repo = ContentItemRepository(db)
    signal_repo = SignalURLRepository(db)
    article_hydrator = ArticleHydrationService()

    # ── 1. Collect raw signal items ───────────────────────────────────────
    all_items: List[SignalItem] = []
    fetchers = [
        ("HN_TOP", fetch_hn_top, {"limit": hn_limit}),
        ("HN_BEST", fetch_hn_best, {"limit": hn_limit}),
        ("GITHUB", fetch_github_trending, {"limit": github_limit}),
        ("YT_TRENDING", _fetch_yt_safe, {"api_key": yt_api_key, "limit": yt_limit}),
    ]
    if discovery_enabled:
        fetchers.append(
            (
                "DISCOVERY_FEEDS",
                fetch_discovery_leads,
                {
                    "limit": discovery_limit,
                    "per_source_limit": discovery_per_source_limit,
                },
            )
        )

    for fetcher_name, fetch_fn, kwargs in fetchers:
        try:
            items = fetch_fn(**kwargs)
            all_items.extend(items)
            logger.info("[signal_ingestion] %s returned %d items", fetcher_name, len(items))
        except Exception as exc:
            msg = f"{fetcher_name} fetch failed: {exc}"
            logger.warning("[signal_ingestion] %s", msg)
            result.errors.append(msg)

    result.signal_urls_seen = len(all_items)
    stubs_this_run = 0

    # ── 2. Process each signal item ───────────────────────────────────────
    for item in all_items:
        # Each item runs inside its own savepoint so an IntegrityError only
        # rolls back that single item, not the whole batch already flushed.
        try:
            with db.begin_nested():  # SAVEPOINT sp_N
                canonical = normalize_url(item.raw_url)
                if not canonical:
                    _record_candidate_audit_event(
                        db,
                        canonical_url=item.raw_url or "",
                        signal_source=item.signal_source,
                        event_type="rejected",
                        reason="normalization_failed",
                    )
                    continue
                if not is_allowed_domain(canonical, channel="signal"):
                    result.domain_rejected += 1
                    logger.debug(
                        "[signal_ingestion] domain policy rejected url=%s source=%s",
                        canonical,
                        item.signal_source.value,
                    )
                    continue

                # Atomic upsert into signal_urls
                signal_row = signal_repo.upsert(
                    raw_url=item.raw_url,
                    canonical_url=canonical,
                    signal_source=item.signal_source,
                    raw_title=item.raw_title,
                    signal_score=item.signal_score,
                )

                is_new_signal = signal_row.hit_count == 1
                if is_new_signal:
                    result.signal_urls_added += 1

                # Check for existing content
                existing = _find_existing(content_repo, canonical)

                if existing is not None:
                    # Content already in the system – increment signal_hits
                    result.signal_hits_bumped += 1
                    existing.signal_hits = (existing.signal_hits or 0) + 1
                    signal_repo.mark_duplicate(signal_row, existing.id)
                    _record_candidate_audit_event(
                        db,
                        canonical_url=canonical,
                        signal_source=item.signal_source,
                        event_type="duplicate",
                        discovered_via=_SIGNAL_SOURCE_LABELS.get(item.signal_source, "signal"),
                        content_item_id=existing.id,
                    )
                    continue

                # New URL – create a CANDIDATE stub (if within per-run cap)
                if stubs_this_run >= max_stubs:
                    result.stubs_skipped += 1
                    _record_candidate_audit_event(
                        db,
                        canonical_url=canonical,
                        signal_source=item.signal_source,
                        event_type="skipped",
                        discovered_via=_SIGNAL_SOURCE_LABELS.get(item.signal_source, "signal"),
                        reason="max_stubs_reached",
                    )
                    continue

                content_type = _detect_content_type(canonical)
                stub = _build_candidate_stub(canonical, item, content_type)
                db.add(stub)
                db.flush()

                if (
                    stub.curation_status == ContentStatus.PROMOTED
                    and content_type == ContentType.ARTICLE
                    and article_hydrator.needs_hydration(stub)
                ):
                    article_hydrator.hydrate_article_candidate(stub)

                signal_repo.mark_ingested(signal_row, stub.id)
                stubs_this_run += 1
                result.stubs_created += 1
                _record_candidate_audit_event(
                    db,
                    canonical_url=canonical,
                    signal_source=item.signal_source,
                    event_type="created",
                    discovered_via=stub.discovered_via,
                    content_item_id=stub.id,
                    payload={
                        "content_type": content_type.value,
                        "signal_score": item.signal_score,
                    },
                )

        except IntegrityError:
            # Savepoint already rolled back by context manager exit.
            # Only this item's changes are lost; prior items in batch are safe.
            result.stubs_skipped += 1
            logger.debug("[signal_ingestion] IntegrityError (duplicate) for %s", item.raw_url)
        except Exception as exc:
            msg = f"Error processing {item.raw_url}: {exc}"
            logger.warning("[signal_ingestion] %s", msg)
            result.errors.append(msg)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        msg = f"Commit failed: {exc}"
        logger.error("[signal_ingestion] %s", msg)
        result.errors.append(msg)

    logger.info(
        "[signal_ingestion] Done. seen=%d added=%d stubs=%d bumped=%d rejected=%d errors=%d",
        result.signal_urls_seen,
        result.signal_urls_added,
        result.stubs_created,
        result.signal_hits_bumped,
        result.domain_rejected,
        len(result.errors),
    )
    return result


def _fetch_yt_safe(*, api_key: Optional[str], limit: int) -> List[SignalItem]:
    """Wrapper so YouTube fetch sits in the same try/except pattern."""
    return fetch_yt_trending(api_key=api_key, max_results=limit)
