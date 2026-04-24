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
    1. Calls each signal fetcher (HN, GitHub, YouTube, optional discovery feeds,
       optional X signal amplification).
    2. Canonicalizes discovered URLs.
    3. Creates CANDIDATE content stubs for URLs not already in ``content_items``.
    4. Bumps ``signal_hits`` on items already in the system.

    All new stubs start as CANDIDATE (invisible in feeds) until the
    ``run_promotion_job`` elevates them to PROMOTED.

    X signals are additive only. If the ``x_signals`` feature flag is disabled
    or ``X_BEARER_TOKEN`` is not set, X is silently skipped and all other
    signal sources continue normally.
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

        # ── X (Twitter) signal parameters ────────────────────────────────────
        # Two-layer guard: feature flag must be enabled AND a bearer token must
        # be configured AND mode must not be "off".
        x_flag_enabled: bool = feature_flags.is_enabled("x_signals")
        x_bearer_token: str = getattr(settings, "X_BEARER_TOKEN", "") or ""
        x_signals_mode: str = getattr(settings, "X_SIGNALS_MODE", "cohort") or "cohort"

        x_signals_enabled = (
            x_flag_enabled
            and bool(x_bearer_token)
            and x_signals_mode != "off"
        )

        if x_flag_enabled and not x_signals_enabled:
            # Flag is on but something else is blocking — log clearly.
            if not x_bearer_token:
                logger.info(
                    "[signal_ingestion] X signals feature flag enabled but "
                    "X_BEARER_TOKEN is not set — skipping X signals"
                )
            elif x_signals_mode == "off":
                logger.info(
                    "[signal_ingestion] X signals feature flag enabled but "
                    "X_SIGNALS_MODE=off — skipping X signals"
                )

        # Parse optional domain allowlist.
        x_allowed_domains_raw: str = getattr(settings, "X_SIGNALS_ALLOWED_DOMAINS", "") or ""
        x_allowed_domains = (
            {d.strip().lower() for d in x_allowed_domains_raw.split(",") if d.strip()}
            if x_allowed_domains_raw.strip()
            else None
        )

        # Parse optional cohort/query overrides.
        x_cohort_raw: str = getattr(settings, "X_SIGNALS_COHORT_ACCOUNTS", "") or ""
        x_cohort_accounts = (
            [a.strip() for a in x_cohort_raw.split(",") if a.strip()]
            if x_cohort_raw.strip()
            else None
        )

        x_terms_raw: str = getattr(settings, "X_SIGNALS_QUERY_TERMS", "") or ""
        x_query_terms = (
            [t.strip() for t in x_terms_raw.split(",") if t.strip()]
            if x_terms_raw.strip()
            else None
        )

        if x_signals_enabled:
            logger.info(
                "[signal_ingestion] X signals ENABLED mode=%s limit=%d min_score=%d",
                x_signals_mode,
                int(getattr(settings, "X_SIGNALS_MAX_ITEMS_PER_RUN", 50)),
                int(getattr(settings, "X_SIGNALS_MIN_SCORE", 10)),
            )

        result = run_signal_ingestion(
            db,
            yt_api_key=yt_api_key or None,
            discovery_enabled=discovery_enabled,
            discovery_limit=discovery_limit,
            discovery_per_source_limit=discovery_per_source_limit,
            x_signals_enabled=x_signals_enabled,
            x_bearer_token=x_bearer_token or None,
            x_signals_mode=x_signals_mode,
            x_signals_limit=int(getattr(settings, "X_SIGNALS_MAX_ITEMS_PER_RUN", 50)),
            x_signals_min_score=int(getattr(settings, "X_SIGNALS_MIN_SCORE", 10)),
            x_signals_allowed_domains=x_allowed_domains,
            x_signals_rate_limit_enabled=bool(
                getattr(settings, "X_SIGNALS_RATE_LIMIT_ENABLED", True)
            ),
            x_signals_max_tweet_age_hours=int(
                getattr(settings, "X_SIGNALS_MAX_TWEET_AGE_HOURS", 24)
            ),
            x_signals_request_timeout=int(
                getattr(settings, "X_SIGNALS_REQUEST_TIMEOUT", 15)
            ),
            x_signals_debug_logging=bool(
                getattr(settings, "X_SIGNALS_DEBUG_LOGGING", False)
            ),
            x_signals_cohort_accounts=x_cohort_accounts,
            x_signals_query_terms=x_query_terms,
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
            deadlock_errors = [err for err in result.errors if "deadlock" in err.lower()]
            if deadlock_errors:
                from app.services.alerting_service import AlertSeverity, alert_scheduler_job_issue

                alert_scheduler_job_issue(
                    job_id="signal_ingestion",
                    issue_type="deadlock_detected",
                    details=deadlock_errors[0],
                    severity=AlertSeverity.CRITICAL,
                )

    except Exception as exc:
        stats.errors.append(str(exc))
        logger.error("[signal_ingestion] Fatal error: %s", exc)
        db.rollback()
    finally:
        db.close()
        stats.complete()
        stats.log_summary()
