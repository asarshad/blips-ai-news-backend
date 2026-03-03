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
