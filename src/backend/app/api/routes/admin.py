"""Admin routes for feature flag management.

These endpoints allow instant feature flag updates without redeploy.
Should be protected in production (e.g., behind internal network or auth).
"""

import importlib
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import require_admin_key
from app.core.dependencies import get_db, get_redis
from app.core.feature_flags import FeatureFlags, get_feature_flags
from app.core.logging import get_logger
from app.core.youtube_quota import YouTubeQuotaBudget
from app.schemas.ads import AdsConfigAdminResponse, AdsRuntimeConfigPatch
from app.schemas.push import PushConfigAdminResponse, PushRuntimeConfigPatch, PushSendResponse
from app.services.ad_config_service import AdConfigService
from app.services.playlist_service import refresh_cached_playlist_items
from app.services.push_config_service import PushConfigService
from app.services.push_service import (
    PushNotificationError,
    PushNotificationService,
    create_push_messaging_client,
)

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(require_admin_key)])


def get_ad_config_service(redis_client=Depends(get_redis)) -> AdConfigService:
    """Provide the runtime ad config service."""
    return AdConfigService(redis_client=redis_client)


def get_push_config_service(redis_client=Depends(get_redis)) -> PushConfigService:
    """Provide the runtime push config service."""
    return PushConfigService(redis_client=redis_client)


def _push_provider_ready() -> bool:
    """Return whether the configured push transport is usable."""
    return create_push_messaging_client().is_available


class FeatureFlagUpdate(BaseModel):
    """Request body for updating a feature flag."""

    enabled: bool


class FeatureFlagResponse(BaseModel):
    """Response for a single feature flag."""

    feature: str
    enabled: bool
    source: str


class ArticleImageRecoveryEvalRequest(BaseModel):
    """Admin request body for evaluating LLM article image recovery."""

    lookback_days: Optional[int] = None
    limit: int = 200
    sample_size: int = 20
    apply: bool = False


class ArticleImageRepairRequest(BaseModel):
    """Admin request body for repairing one article image by content ID."""

    content_id: int


class ContentTouchRequest(BaseModel):
    """Admin request body for targeted content touch invalidation."""

    content_ids: list[int] = Field(default_factory=list)


class BrokenVideoSummaryRepairRequest(BaseModel):
    """Admin request body for repairing malformed persisted video summaries."""

    hours_back: Optional[int] = None
    limit: int = 250
    dry_run: bool = False


class ScriptedMaintenanceRequest(BaseModel):
    """Admin request body for the reusable scripted maintenance hook."""

    payload: Dict[str, Any] = Field(default_factory=dict)


@router.get("/ads/config", response_model=AdsConfigAdminResponse)
def get_ads_config(
    ad_config_service: AdConfigService = Depends(get_ad_config_service),
):
    """Return the raw runtime ads config and its source."""
    config, source = ad_config_service.get_raw_config()
    return AdsConfigAdminResponse(ads=config, source=source)


@router.patch("/ads/config", response_model=AdsConfigAdminResponse)
def patch_ads_config(
    update: AdsRuntimeConfigPatch,
    ad_config_service: AdConfigService = Depends(get_ad_config_service),
):
    """Update runtime ads config with a partial patch."""
    try:
        config = ad_config_service.update_config(update)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("Runtime ads config updated")
    return AdsConfigAdminResponse(ads=config, source="redis")


@router.delete("/ads/config", response_model=AdsConfigAdminResponse)
def reset_ads_config(
    ad_config_service: AdConfigService = Depends(get_ad_config_service),
):
    """Reset runtime ads config to environment defaults."""
    try:
        config = ad_config_service.reset_config()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("Runtime ads config reset to defaults")
    return AdsConfigAdminResponse(ads=config, source="default")


@router.get("/push/config", response_model=PushConfigAdminResponse)
def get_push_config(
    push_config_service: PushConfigService = Depends(get_push_config_service),
):
    """Return the raw runtime push config and provider status."""
    config, source = push_config_service.get_raw_config()
    return PushConfigAdminResponse(
        push=config,
        source=source,
        provider_ready=_push_provider_ready(),
    )


@router.patch("/push/config", response_model=PushConfigAdminResponse)
def patch_push_config(
    update: PushRuntimeConfigPatch,
    push_config_service: PushConfigService = Depends(get_push_config_service),
):
    """Update runtime push config with a partial patch."""
    try:
        config = push_config_service.update_config(update)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("Runtime push config updated")
    return PushConfigAdminResponse(
        push=config,
        source="redis",
        provider_ready=_push_provider_ready(),
    )


@router.delete("/push/config", response_model=PushConfigAdminResponse)
def reset_push_config(
    push_config_service: PushConfigService = Depends(get_push_config_service),
):
    """Reset runtime push config to environment defaults."""
    try:
        config = push_config_service.reset_config()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("Runtime push config reset to defaults")
    return PushConfigAdminResponse(
        push=config,
        source="default",
        provider_ready=_push_provider_ready(),
    )


@router.post("/push/send/{content_id}", response_model=PushSendResponse)
def send_push_now(
    content_id: int,
    db: Session = Depends(get_db),
):
    """Manually send a push notification for a promoted article/video."""
    try:
        service = PushNotificationService(db=db)
        return service.send_manual(content_id=content_id, actor="admin")
    except PushNotificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/flags", response_model=Dict[str, Any])
def get_all_feature_flags(flags: FeatureFlags = Depends(get_feature_flags)):
    """
    Get status of all feature flags.

    Returns dict with each feature's current status and source (redis/env/default).
    """
    return flags.get_all_flags()


@router.get("/flags/{feature}", response_model=FeatureFlagResponse)
def get_feature_flag(feature: str, flags: FeatureFlags = Depends(get_feature_flags)):
    """
    Get status of a specific feature flag.

    Args:
        feature: Feature name (e.g., "chat", "ingestion")
    """
    all_flags = flags.get_all_flags()

    if feature.lower() not in all_flags:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown feature: {feature}. Known features: {list(all_flags.keys())}",
        )

    flag_info = all_flags[feature.lower()]
    return FeatureFlagResponse(
        feature=feature.lower(), enabled=flag_info["enabled"], source=flag_info["source"]
    )


@router.put("/flags/{feature}", response_model=FeatureFlagResponse)
def update_feature_flag(
    feature: str, update: FeatureFlagUpdate, flags: FeatureFlags = Depends(get_feature_flags)
):
    """
    Update a feature flag (stored in Redis for instant effect).

    Args:
        feature: Feature name (e.g., "chat", "ingestion")
        update: New enabled status
    """
    feature = feature.lower()

    success = flags.set_flag(feature, update.enabled)
    if not success:
        raise HTTPException(
            status_code=500, detail="Failed to update feature flag (Redis unavailable)"
        )

    logger.info(f"Feature flag '{feature}' updated to {update.enabled}")

    return FeatureFlagResponse(feature=feature, enabled=update.enabled, source="redis")


@router.delete("/flags/{feature}")
def reset_feature_flag(feature: str, flags: FeatureFlags = Depends(get_feature_flags)):
    """
    Reset a feature flag to its default value (removes Redis override).

    Args:
        feature: Feature name
    """
    feature = feature.lower()

    success = flags.delete_flag(feature)
    if not success:
        raise HTTPException(
            status_code=500, detail="Failed to reset feature flag (Redis unavailable)"
        )

    # Get the new value (will be from env or default)
    new_value = flags.is_enabled(feature)

    logger.info(f"Feature flag '{feature}' reset to default ({new_value})")

    return {
        "feature": feature,
        "enabled": new_value,
        "source": "env" if flags._get_from_env(feature) is not None else "default",
        "message": "Feature flag reset to default",
    }


# =============================================================================
# Manual Triggers (Dev/Testing)
# =============================================================================


@router.get("/stats/content")
def get_content_stats():
    """Get content statistics for monitoring."""
    from sqlalchemy import text

    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        # Count by type
        result = db.execute(
            text("""
            SELECT type, COUNT(*) as count
            FROM content_items
            GROUP BY type
        """)
        )

        by_type = {row[0]: row[1] for row in result}

        # Count duplicates
        result = db.execute(
            text("""
            SELECT COUNT(*) FROM (
                SELECT source_url
                FROM content_items
                GROUP BY source_url
                HAVING COUNT(*) > 1
            ) AS duplicates
        """)
        )
        duplicate_count = result.scalar() or 0

        # Recent 24h
        result = db.execute(
            text("""
            SELECT type, COUNT(*) as count
            FROM content_items
            WHERE created_at > NOW() - INTERVAL '24 hours'
            GROUP BY type
        """)
        )
        recent_24h = {row[0]: row[1] for row in result}

        # AI processing stats
        result = db.execute(
            text("""
            SELECT
                COUNT(*) FILTER (WHERE ai_processed = true)  AS processed,
                COUNT(*) FILTER (WHERE ai_processed = false) AS unprocessed
            FROM content_items
        """)
        )
        ai_row = result.fetchone()
        ai_stats = {"processed": ai_row[0], "unprocessed": ai_row[1]} if ai_row else {}

        return {
            "total_by_type": by_type,
            "total": sum(by_type.values()),
            "duplicate_urls": duplicate_count,
            "last_24h": recent_24h,
            "ai_processing": ai_stats,
        }

    finally:
        db.close()


@router.post("/trigger-fetch")
def trigger_fetch():
    """Manually trigger content ingestion + AI summarization."""
    from app.scheduler.tasks import fetch_and_process_news

    try:
        fetch_and_process_news()
        return {
            "status": "triggered",
            "message": "Ingestion + AI summarization triggered successfully",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/trigger-summarize")
def trigger_summarize():
    """Manually trigger AI summarization for unprocessed content."""
    from app.scheduler.tasks_ai_retry import process_ai_summaries

    try:
        process_ai_summaries()
        return {"status": "triggered", "message": "AI summarization triggered successfully"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/trigger-image-repair")
def trigger_image_repair():
    """Manually trigger the article image repair backfill.

    Re-fetches images for articles whose stored image is missing, generic,
    suspicious, or a known origin-bound proxy URL (/_next/image, etc.).
    """
    from app.db.base import SessionLocal
    from app.services.article_image_service import repair_article_image_metadata

    db = SessionLocal()
    try:
        result = repair_article_image_metadata(
            db,
            lookback_days=30,
            limit=500,
            include_generic=True,
        )
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.error(f"Image repair failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        db.close()


@router.post("/trigger-article-image-repair")
def trigger_article_image_repair(payload: ArticleImageRepairRequest):
    """Force-refresh image metadata for a specific article row."""
    from app.db.base import SessionLocal
    from app.services.article_image_service import repair_single_article_image

    db = SessionLocal()
    try:
        result = repair_single_article_image(db, content_id=payload.content_id)
        return {"status": "ok", "result": result}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as e:
        db.rollback()
        logger.error("Targeted article image repair failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        db.close()


@router.post("/trigger-image-recovery-eval")
def trigger_image_recovery_eval(payload: ArticleImageRecoveryEvalRequest):
    """Evaluate or apply LLM recovery on promoted article rows with blank images."""
    from app.db.base import SessionLocal
    from app.services.article_image_service import evaluate_llm_article_image_recovery

    db = SessionLocal()
    try:
        result = evaluate_llm_article_image_recovery(
            db,
            lookback_days=payload.lookback_days,
            limit=payload.limit,
            sample_size=payload.sample_size,
            apply=payload.apply,
        )
        return {"status": "ok", "result": result}
    except Exception as e:
        db.rollback()
        logger.error("Image recovery evaluation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        db.close()


@router.post("/trigger-broken-video-summary-repair")
def trigger_broken_video_summary_repair(payload: BrokenVideoSummaryRepairRequest):
    """Dry-run or execute repair for persisted broken video summaries."""
    from scripts.repair_broken_video_summaries import (
        find_matching_broken_video_summary_ids,
        repair_broken_video_summaries,
    )

    try:
        matches = find_matching_broken_video_summary_ids(
            hours_back=payload.hours_back,
            limit=payload.limit,
        )
        if payload.dry_run:
            scope = f"last {payload.hours_back}h" if payload.hours_back is not None else "all time"
            return {
                "status": "ok",
                "result": {
                    "scope": scope,
                    "matched": len(matches),
                    "sample_ids": matches[:10],
                    "dry_run": True,
                },
            }

        result = repair_broken_video_summaries(
            hours_back=payload.hours_back,
            limit=payload.limit,
        )
        return {"status": "ok", "result": result.__dict__}
    except Exception as e:
        logger.error("Broken video summary repair failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/trigger-scripted-maintenance")
def trigger_scripted_maintenance(payload: Optional[ScriptedMaintenanceRequest] = None):
    """Run the current operator maintenance script.

    This endpoint intentionally delegates to a single stable script module so
    operators can replace that script's contents for new backfills/repairs
    without needing a new API route every time.
    """
    try:
        module = importlib.import_module("scripts.operator_backfill_job")
        module = importlib.reload(module)
        if not hasattr(module, "run"):
            raise RuntimeError("scripts.operator_backfill_job is missing run(payload)")

        result = module.run((payload.payload if payload else None) or {})
        return {
            "status": "ok",
            "script": "scripts.operator_backfill_job",
            "result": result,
        }
    except Exception as e:
        logger.error("Scripted maintenance failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/trigger-content-touch")
def trigger_content_touch(payload: Optional[ContentTouchRequest] = None):
    """Force-bump updated_at for recently repaired/fixed content, then bust feed cache.

    Touches:
    - Articles created in the last 30 days that have a non-empty image_url
      (covers all image-repair beneficiaries).
    - Videos/Reels created in the last 30 days that are ai_processed and have
      a non-empty summary (covers all video summary fix beneficiaries).

    This causes the feed_version hash to change so mobile clients detect
    new content on their next poll and pull down the corrected data.
    """
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from app.db.base import SessionLocal
    from app.models.content import ContentItem, ContentType
    from app.services.tiered_feed_service import invalidate_tiered_feed_cache

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        cutoff = now - timedelta(days=30)
        targeted_ids = sorted(
            {
                int(content_id)
                for content_id in ((payload.content_ids if payload else []) or [])
                if int(content_id) > 0
            }
        )

        if targeted_ids:
            touch_result = db.execute(
                update(ContentItem).where(ContentItem.id.in_(targeted_ids)).values(updated_at=now)
            )
            db.commit()
            invalidate_tiered_feed_cache()
            cache_refresh = refresh_cached_playlist_items(db, content_ids=targeted_ids)
            return {
                "status": "ok",
                "mode": "targeted",
                "content_ids": targeted_ids,
                "content_items_touched": touch_result.rowcount,
                "cache_busted": True,
                "playlist_cache_refresh": cache_refresh,
            }

        article_result = db.execute(
            update(ContentItem)
            .where(
                ContentItem.type == ContentType.ARTICLE,
                ContentItem.created_at >= cutoff,
                ContentItem.image_url.isnot(None),
                ContentItem.image_url != "",
            )
            .values(updated_at=now)
        )

        video_result = db.execute(
            update(ContentItem)
            .where(
                ContentItem.type.in_([ContentType.VIDEO, ContentType.REEL]),
                ContentItem.created_at >= cutoff,
                ContentItem.ai_processed.is_(True),
                ContentItem.summary.isnot(None),
                ContentItem.summary != "",
            )
            .values(updated_at=now)
        )

        db.commit()
        articles_touched = article_result.rowcount
        videos_touched = video_result.rowcount

        invalidate_tiered_feed_cache()

        return {
            "status": "ok",
            "articles_touched": articles_touched,
            "videos_touched": videos_touched,
            "cache_busted": True,
        }
    except Exception as e:
        db.rollback()
        logger.error("trigger-content-touch failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        db.close()


@router.post("/youtube/reset-search-cooldown")
def reset_youtube_search_cooldown(surface: Optional[str] = None):
    """Clear Redis cooldown keys so operators can force a new discovery sweep."""
    from app.core.dependencies import get_redis

    normalized = (surface or "").strip().lower()
    if normalized and normalized not in {"videos", "reels"}:
        raise HTTPException(status_code=400, detail="surface must be 'videos' or 'reels'")

    surfaces = [normalized] if normalized else ["videos", "reels"]
    keys = [f"blips:youtube:search:cooldown:{surface_name}" for surface_name in surfaces]

    try:
        redis_client = get_redis()
        deleted = redis_client.delete(*keys) if keys else 0
        quota = YouTubeQuotaBudget(redis_client=redis_client)
    except Exception as exc:
        logger.error("Failed to reset YouTube search cooldown: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to reset YouTube cooldown") from exc

    return {
        "status": "ok",
        "surfaces": surfaces,
        "deleted_keys": deleted,
        "quota_remaining": {
            "all": quota.remaining_units(),
            "search": quota.remaining_units(bucket="search"),
            "duration": quota.remaining_units(bucket="duration"),
        },
        "lockout_active": quota.is_locked_out(),
    }


# ------------------------------------------------------------------
# Maintenance / Retention endpoints
# ------------------------------------------------------------------


@router.post("/maintenance/cleanup")
def manual_cleanup():
    """
    Manually trigger data retention cleanup.

    Uses the same retention service as the daily scheduler.
    Returns counts of deleted rows per table.
    """
    from app.scheduler.tasks_cleanup import run_data_cleanup_job

    try:
        result = run_data_cleanup_job()
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.error(f"Manual cleanup failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/maintenance/status")
def cleanup_status():
    """
    Return last cleanup run info and current retention config.
    """
    from app.core.config import settings as _settings

    last_run_at = None
    try:
        from app.core.dependencies import get_redis

        r = get_redis()
        raw = r.get("blips:cleanup:last_run_at")
        if raw:
            last_run_at = raw.decode() if isinstance(raw, bytes) else raw
    except Exception:
        pass

    return {
        "last_run_at": last_run_at,
        "retention_policy": {
            "content_items_days": _settings.RETAIN_CONTENT_DAYS,
            "ingestion_progress_days": _settings.RETAIN_INGESTION_PROGRESS_DAYS,
            "events_days": _settings.RETAIN_EVENTS_DAYS,
            "conversations_days": _settings.RETAIN_CONVERSATIONS_DAYS,
            "usage_days": _settings.RETAIN_USAGE_DAYS,
            "editorial_actions_days": _settings.RETAIN_EDITORIAL_DAYS,
        },
    }
