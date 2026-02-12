"""Scheduled cleanup tasks for data retention compliance.

Purges expired records from user-facing tables according to the
retention periods defined in the privacy policy:
  - usage:              90 days
  - interaction_events: 90 days
  - conversations:      30 days
"""

from datetime import datetime, timedelta

from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.models.content import InteractionEvent
from app.models.conversation import Conversation
from app.models.usage import Usage
from app.scheduler.job_stats import log_job_start

logger = get_logger(__name__)

# Retention periods (days)
USAGE_RETENTION_DAYS = 90
INTERACTION_RETENTION_DAYS = 90
CONVERSATION_RETENTION_DAYS = 30


def run_data_cleanup_job() -> None:
    """Purge records older than retention thresholds."""
    stats = log_job_start("data_cleanup")
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        total = 0

        # --- usage (90 days) ---
        usage_cutoff = now - timedelta(days=USAGE_RETENTION_DAYS)
        usage_deleted = (
            db.query(Usage)
            .filter(Usage.timestamp < usage_cutoff)
            .delete(synchronize_session=False)
        )
        total += usage_deleted
        logger.info(f"[data_cleanup] Deleted {usage_deleted} usage rows older than {usage_cutoff.date()}")

        # --- interaction_events (90 days) ---
        interaction_cutoff = now - timedelta(days=INTERACTION_RETENTION_DAYS)
        interaction_deleted = (
            db.query(InteractionEvent)
            .filter(InteractionEvent.created_at < interaction_cutoff)
            .delete(synchronize_session=False)
        )
        total += interaction_deleted
        logger.info(
            f"[data_cleanup] Deleted {interaction_deleted} interaction_events rows older than {interaction_cutoff.date()}"
        )

        # --- conversations (30 days) ---
        conversation_cutoff = now - timedelta(days=CONVERSATION_RETENTION_DAYS)
        conversation_deleted = (
            db.query(Conversation)
            .filter(Conversation.timestamp < conversation_cutoff)
            .delete(synchronize_session=False)
        )
        total += conversation_deleted
        logger.info(
            f"[data_cleanup] Deleted {conversation_deleted} conversation rows older than {conversation_cutoff.date()}"
        )

        db.commit()
        stats.items_processed = total
        logger.info(f"[data_cleanup] Total rows purged: {total}")

    except Exception as e:
        db.rollback()
        stats.errors.append(str(e))
        logger.error(f"[data_cleanup] Error: {e}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()
