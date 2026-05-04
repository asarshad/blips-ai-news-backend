"""
Ingestion health monitoring task.

Checks for stalled ingestion and triggers alerts when no content
has been ingested for an extended period.
"""

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.db.base import SessionLocal

logger = get_logger(__name__)

# Alert if no ingestion in this many hours
STALL_THRESHOLD_HOURS = 2.0


def check_ingestion_health() -> None:
    """
    Check for ingestion stall and alert if needed.

    Runs periodically (every 30 min) to detect ingestion issues.
    Triggers alert if no new content in past 2 hours.
    """
    logger.info("Running ingestion health check")

    db = SessionLocal()
    try:
        from sqlalchemy import func

        from app.models.content import ContentItem

        # Get timestamp of most recent content
        most_recent = db.query(func.max(ContentItem.created_at)).scalar()

        now = datetime.now(timezone.utc)

        if most_recent is None:
            logger.warning("No content found in database - fresh install?")
            return

        # Ensure timezone-aware comparison
        if most_recent.tzinfo is None:
            most_recent = most_recent.replace(tzinfo=timezone.utc)

        hours_since_ingestion = (now - most_recent).total_seconds() / 3600

        # Log current status
        logger.info(
            f"Last ingestion: {most_recent.isoformat()}, hours ago: {hours_since_ingestion:.1f}"
        )

        # Check if stalled
        if hours_since_ingestion >= STALL_THRESHOLD_HOURS:
            logger.warning(
                f"Ingestion appears stalled - no content for {hours_since_ingestion:.1f} hours"
            )

            # Send alert
            try:
                from app.services.alerting_service import alert_ingestion_stalled

                alert_ingestion_stalled(
                    last_success_at=most_recent,
                    hours_since_ingestion=hours_since_ingestion,
                )
            except Exception as e:
                logger.error(f"Failed to send ingestion stall alert: {e}")
        else:
            logger.info("Ingestion health OK")

    except Exception as e:
        logger.error(f"Error checking ingestion health: {e}", exc_info=True)
    finally:
        db.close()


def check_inventory_health() -> None:
    """Check per-surface inventory health and alert on degraded freshness."""
    logger.info("Running inventory health check")

    db = SessionLocal()
    try:
        from app.services.alerting_service import alert_inventory_surface_degraded
        from app.services.inventory_service import get_cached_inventory_health

        health = get_cached_inventory_health(db, force_refresh=True)
        unhealthy_surfaces = []

        for surface, metrics in health.surfaces.items():
            if metrics.is_healthy:
                continue

            unhealthy_surfaces.append(surface.value)
            logger.warning(
                "Inventory degraded for %s: %s",
                surface.value,
                "; ".join(metrics.issues) or "unknown issue",
            )

            # Only alert when refresh count is meaningfully below threshold (< 75%).
            # A minor miss (e.g. 10 vs 12) is normal daily variation and not actionable.
            if metrics.recent_refresh_threshold > 0:
                refresh_ratio = metrics.recent_refresh_count / metrics.recent_refresh_threshold
                if refresh_ratio >= 0.75:
                    logger.info(
                        "Suppressing inventory alert for %s: refresh %d/%d is within tolerance",
                        surface.value,
                        metrics.recent_refresh_count,
                        metrics.recent_refresh_threshold,
                    )
                    continue

            try:
                alert_inventory_surface_degraded(
                    surface=surface.value,
                    issues=metrics.issues,
                    recent_refresh_count=metrics.recent_refresh_count,
                    recent_refresh_threshold=metrics.recent_refresh_threshold,
                    newest_item_age_seconds=metrics.newest_item_age_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to send inventory alert for %s: %s", surface.value, exc)

        if not unhealthy_surfaces:
            logger.info("Inventory health OK")

    except Exception as e:
        logger.error(f"Error checking inventory health: {e}", exc_info=True)
    finally:
        db.close()


def check_strategic_content_health() -> None:
    """Alert on app-level content supply risks that are too important to miss."""
    logger.info("Running strategic content health check")

    db = SessionLocal()
    try:
        from app.services.strategic_content_health_service import (
            compute_strategic_content_health,
            emit_strategic_content_alerts,
        )

        health = compute_strategic_content_health(db)
        issues = health.get("issues") or []
        if issues:
            logger.warning(
                "Strategic content health degraded status=%s issues=%s",
                health.get("status"),
                [issue.get("key") for issue in issues],
            )
            sent = emit_strategic_content_alerts(health)
            logger.info("Strategic content alerts sent=%s", sent)
        else:
            logger.info("Strategic content health OK")
    except Exception as exc:  # noqa: BLE001
        logger.error("Error checking strategic content health: %s", exc, exc_info=True)
    finally:
        db.close()


def get_ingestion_metrics() -> dict:
    """
    Get ingestion metrics for the /metrics endpoint.

    Returns:
        Dict with ingestion health metrics
    """
    db = SessionLocal()
    try:
        from datetime import timedelta

        from sqlalchemy import func

        from app.models.content import ContentItem, ContentType

        now = datetime.now(timezone.utc)
        two_hours_ago = now - timedelta(hours=2)
        twenty_four_hours_ago = now - timedelta(hours=24)

        # Most recent content
        most_recent = db.query(func.max(ContentItem.created_at)).scalar()

        # Counts in last 2 hours
        articles_2h = (
            db.query(func.count(ContentItem.id))
            .filter(
                ContentItem.type == ContentType.ARTICLE,
                ContentItem.created_at >= two_hours_ago,
            )
            .scalar()
            or 0
        )

        videos_2h = (
            db.query(func.count(ContentItem.id))
            .filter(
                ContentItem.type == ContentType.VIDEO,
                ContentItem.created_at >= two_hours_ago,
            )
            .scalar()
            or 0
        )

        # Counts in last 24 hours
        articles_24h = (
            db.query(func.count(ContentItem.id))
            .filter(
                ContentItem.type == ContentType.ARTICLE,
                ContentItem.created_at >= twenty_four_hours_ago,
            )
            .scalar()
            or 0
        )

        videos_24h = (
            db.query(func.count(ContentItem.id))
            .filter(
                ContentItem.type == ContentType.VIDEO,
                ContentItem.created_at >= twenty_four_hours_ago,
            )
            .scalar()
            or 0
        )

        hours_since = None
        if most_recent:
            if most_recent.tzinfo is None:
                most_recent = most_recent.replace(tzinfo=timezone.utc)
            hours_since = (now - most_recent).total_seconds() / 3600

        return {
            "last_successful_ingestion_at": most_recent.isoformat() if most_recent else None,
            "hours_since_last_ingestion": round(hours_since, 2) if hours_since else None,
            "is_stalled": hours_since >= STALL_THRESHOLD_HOURS if hours_since else False,
            "articles_ingested_last_2h": articles_2h,
            "videos_ingested_last_2h": videos_2h,
            "articles_ingested_last_24h": articles_24h,
            "videos_ingested_last_24h": videos_24h,
        }

    except Exception as e:
        logger.error(f"Error getting ingestion metrics: {e}", exc_info=True)
        return {
            "error": str(e),
            "last_successful_ingestion_at": None,
        }
    finally:
        db.close()
