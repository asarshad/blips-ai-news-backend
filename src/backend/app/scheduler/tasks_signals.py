"""Scheduled tasks for signal ingestion (Coverage Guarantee pipeline)."""

from __future__ import annotations

from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import log_job_start

logger = get_logger(__name__)


def run_signal_ingestion_job() -> None:
    """Fetch trend/discovery signals and enqueue new URLs.

    Cadence: every 60 minutes (configurable via ``SIGNAL_INTERVAL_MINUTES``).

    The job:
    1. Calls each signal fetcher (HN, GitHub, YouTube, optional discovery feeds).
    2. Canonicalizes discovered URLs.
    3. Creates CANDIDATE content stubs for URLs not already in ``content_items``.
    4. Bumps ``signal_hits`` on items already in the system.

    All new stubs start as CANDIDATE (invisible in feeds) until the
    ``run_promotion_job`` elevates them to PROMOTED.
    """
    if not feature_flags.is_enabled("signals"):
        logger.info("[signal_ingestion] SKIPPED – 'signals' feature flag disabled")
        return

    stats = log_job_start("signal_ingestion")
    db = SessionLocal()
    try:
        from app.core.config import get_settings
        from app.ingestion.signal_ingestion import run_signal_ingestion
        from app.scheduler.tasks_content_events import run_content_event_dispatch_job

        settings = get_settings()
        yt_api_key: str = getattr(settings, "YOUTUBE_API_KEY", "") or ""
        discovery_enabled: bool = bool(getattr(settings, "DISCOVERY_SIGNAL_ENABLED", True))
        discovery_limit: int = int(getattr(settings, "DISCOVERY_SIGNAL_LIMIT", 25))
        discovery_per_source_limit: int = int(
            getattr(settings, "DISCOVERY_SIGNAL_PER_SOURCE_LIMIT", 5)
        )

        result = run_signal_ingestion(
            db,
            yt_api_key=yt_api_key or None,
            discovery_enabled=discovery_enabled,
            discovery_limit=discovery_limit,
            discovery_per_source_limit=discovery_per_source_limit,
        )
        run_content_event_dispatch_job()

        stats.items_processed = result.stubs_created + result.signal_hits_bumped
        logger.info(
            "[signal_ingestion] seen=%d added_signal=%d stubs=%d bumped=%d errors=%d",
            result.signal_urls_seen,
            result.signal_urls_added,
            result.stubs_created,
            result.signal_hits_bumped,
            len(result.errors),
        )
        if result.errors:
            stats.errors.extend(result.errors)

    except Exception as exc:
        stats.errors.append(str(exc))
        logger.error("[signal_ingestion] Fatal error: %s", exc)
        db.rollback()
    finally:
        db.close()
        stats.complete()
        stats.log_summary()
