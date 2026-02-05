"""Curation-related scheduled tasks (scoring, clustering, preference decay)."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger

from app.core.feature_flags import feature_flags
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.scheduler.job_stats import log_job_start

logger = get_logger(__name__)


def run_scoring_job():
    stats = log_job_start("scoring")

    db = SessionLocal()
    try:
        from app.repositories.content_repo import ContentItemRepository
        from app.repositories.user_repo import InteractionEventRepository
        from app.services.scoring_service import ScoringService

        content_repo = ContentItemRepository(db)
        event_repo = InteractionEventRepository(db)

        scoring = ScoringService(content_repo, event_repo)
        result = scoring.run_scoring_job(hours_back=72)

        stats.items_processed = result.get("items_scored", 0) if isinstance(result, dict) else 0
        logger.info(f"[scoring] Result: {result}")

    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[scoring] Error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()


def run_clustering_job():
    if not feature_flags.is_enabled("clustering"):
        logger.info("[clustering] SKIPPED - clustering feature is disabled")
        return

    stats = log_job_start("clustering")

    db = SessionLocal()
    try:
        from app.repositories.content_repo import ContentItemRepository
        from app.services.clustering_service import ClusteringService

        content_repo = ContentItemRepository(db)
        clustering = ClusteringService(content_repo)
        result = clustering.run_clustering_job()

        stats.items_processed = result.get("items_clustered", 0) if isinstance(result, dict) else 0
        logger.info(f"[clustering] Result: {result}")

    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[clustering] Error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()


def run_preference_decay_job():
    if not feature_flags.is_enabled("personalization"):
        logger.info("[preference_decay] SKIPPED - personalization feature is disabled")
        return

    stats = log_job_start("preference_decay")

    db = SessionLocal()
    try:
        from app.repositories.content_repo import ContentItemRepository
        from app.repositories.user_repo import (
            InteractionEventRepository,
            UserPreferenceRepository,
            UserProfileRepository,
        )
        from app.services.personalization_service import PersonalizationService

        profile_repo = UserProfileRepository(db)
        preference_repo = UserPreferenceRepository(db)
        event_repo = InteractionEventRepository(db)
        content_repo = ContentItemRepository(db)

        personalization = PersonalizationService(profile_repo, preference_repo, event_repo, content_repo)

        result = personalization.run_decay_job()
        stats.items_processed = result.get("profiles_updated", 0) if isinstance(result, dict) else 0
        logger.info(f"[preference_decay] Result: {result}")

    except Exception as e:
        stats.errors.append(str(e))
        logger.error(f"[preference_decay] Error: {str(e)}")
    finally:
        db.close()
        stats.complete()
        stats.log_summary()


def preference_decay_trigger() -> CronTrigger:
    """Kept for callers that want the canonical schedule."""

    return CronTrigger(hour=3, minute=0)
