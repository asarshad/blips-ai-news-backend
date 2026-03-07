"""
Editorial admin routes.

All endpoints are protected by `require_admin_key` (applied at the
router level in api/admin/__init__.py).
"""

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.admin.schemas import (
    BoostRequest,
    BoostResponse,
    CandidateQueueItem,
    CandidateQueueResponse,
    ContentItemDetail,
    ContentItemSummary,
    EditorialActionRecord,
    PaginatedContentResponse,
    SubmitURLRequest,
    SubmitURLResponse,
    SuppressResponse,
)
from app.core.dependencies import get_db
from app.domain.editorial.service import EditorialService
from app.repositories.editorial_repo import EditorialRepository

router = APIRouter()

ACTOR = "admin"  # In a multi-user setup this would come from the token.


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _to_summary(item) -> ContentItemSummary:
    return ContentItemSummary(
        id=item.id,
        title=item.title,
        source=item.source or "",
        source_url=item.source_url,
        canonical_url=item.canonical_url,
        content_type=item.type.value if item.type else "ARTICLE",
        published_at=item.published_at,
        created_at=item.created_at,
        suppressed=item.is_suppressed,
        editorial_boost=item.editorial_boost or 0,
        manual_added=item.manual_added or False,
        quality_score=item.quality_score,
        cluster_id=item.cluster_id,
        global_score=item.global_score,
    )


def _to_detail(item, actions) -> ContentItemDetail:
    return ContentItemDetail(
        id=item.id,
        title=item.title,
        source=item.source or "",
        source_url=item.source_url,
        canonical_url=item.canonical_url,
        content_type=item.type.value if item.type else "ARTICLE",
        published_at=item.published_at,
        created_at=item.created_at,
        suppressed=item.is_suppressed,
        editorial_boost=item.editorial_boost or 0,
        manual_added=item.manual_added or False,
        quality_score=item.quality_score,
        cluster_id=item.cluster_id,
        global_score=item.global_score,
        description=item.description,
        summary=item.summary,
        image_url=item.image_url,
        video_url=item.video_url,
        topics=item.topics,
        entities=item.entities,
        ai_processed=item.ai_processed,
        dedupe_key=item.dedupe_key,
        canonical_key=item.canonical_key,
        trend_score=item.trend_score,
        recency_score=item.recency_score,
        diversity_boost=item.diversity_boost,
        added_by=item.added_by,
        added_at=item.added_at,
        last_modified_by=item.last_modified_by,
        last_modified_at=item.last_modified_at,
        recent_actions=[
            EditorialActionRecord(
                id=a.id,
                action_type=a.action_type,
                old_value=a.old_value,
                new_value=a.new_value,
                actor=a.actor,
                created_at=a.created_at,
            )
            for a in actions
        ],
    )


def _to_candidate_queue_item(item) -> CandidateQueueItem:
    return CandidateQueueItem(
        id=item.id,
        title=item.title,
        source=item.source or "",
        source_url=item.source_url,
        canonical_url=item.canonical_url,
        content_type=item.type.value if item.type else "ARTICLE",
        published_at=item.published_at,
        created_at=item.created_at,
        suppressed=item.is_suppressed,
        editorial_boost=item.editorial_boost or 0,
        manual_added=item.manual_added or False,
        quality_score=item.quality_score,
        cluster_id=item.cluster_id,
        global_score=item.global_score,
        curation_status=item.curation_status.value if item.curation_status else "CANDIDATE",
        discovered_via=item.discovered_via,
        signal_hits=item.signal_hits or 0,
        promotion_score=item.promotion_score,
        candidate_first_seen_at=getattr(item, "candidate_first_seen_at", None),
        candidate_signal_source=getattr(item, "candidate_signal_source", None),
        candidate_raw_title=getattr(item, "candidate_raw_title", None),
    )


# ------------------------------------------------------------------
# GET  /admin/editorial/content
# ------------------------------------------------------------------


@router.get("/editorial/content", response_model=PaginatedContentResponse)
def list_content(
    day: Optional[str] = Query(None, description="UTC date YYYY-MM-DD"),
    type: Optional[str] = Query(None, description="ARTICLE|VIDEO|REEL"),
    source: Optional[str] = Query(None),
    suppressed: Optional[bool] = Query(None),
    manual_added: Optional[bool] = Query(None),
    sort_by: str = Query("published_at"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List content with editorial filters."""
    from datetime import date as date_type

    parsed_day = None
    if day:
        try:
            parsed_day = date_type.fromisoformat(day)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid day format. Use YYYY-MM-DD"
            ) from None

    repo = EditorialRepository(db)
    items, total = repo.list_content(
        day=parsed_day,
        content_type=type,
        source=source,
        suppressed=suppressed,
        manual_added=manual_added,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )
    pages = max(1, math.ceil(total / page_size))
    return PaginatedContentResponse(
        items=[_to_summary(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get("/editorial/candidates", response_model=CandidateQueueResponse)
def list_candidate_queue(
    type: Optional[str] = Query(None, description="ARTICLE|VIDEO|REEL"),
    source: Optional[str] = Query(None),
    discovered_via: Optional[str] = Query(None),
    min_signal_hits: int = Query(0, ge=0),
    sort_by: str = Query("priority", pattern="^(priority|first_seen|published_at)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List pending CANDIDATE items for editorial review triage."""
    repo = EditorialRepository(db)
    items, total = repo.list_candidate_queue(
        content_type=type,
        source=source,
        discovered_via=discovered_via,
        min_signal_hits=min_signal_hits,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )
    pages = max(1, math.ceil(total / page_size))
    return CandidateQueueResponse(
        items=[_to_candidate_queue_item(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        pending_by_type=repo.candidate_queue_counts(),
    )


# ------------------------------------------------------------------
# GET  /admin/editorial/content/{id}
# ------------------------------------------------------------------


@router.get("/editorial/content/{content_id}", response_model=ContentItemDetail)
def get_content_detail(
    content_id: int,
    db: Session = Depends(get_db),
):
    """Get full content details + recent editorial actions."""
    repo = EditorialRepository(db)
    item = repo.get_content_by_id(content_id)
    if not item:
        raise HTTPException(status_code=404, detail="Content not found")

    actions = repo.get_actions_for_content(content_id, limit=20)
    return _to_detail(item, actions)


# ------------------------------------------------------------------
# POST /admin/editorial/content/submit
# ------------------------------------------------------------------


@router.post("/editorial/content/submit", response_model=SubmitURLResponse)
def submit_url(
    body: SubmitURLRequest,
    db: Session = Depends(get_db),
):
    """
    Manually submit a URL for ingestion.

    Runs through the same canonicalization / dedupe logic as the
    automatic ingestion pipeline.
    """
    svc = EditorialService(db)
    result = svc.submit_url(
        url=body.url,
        importance_level=body.importance_level,
        actor=ACTOR,
    )
    return SubmitURLResponse(
        content_id=result.content_id,
        duplicate=result.duplicate,
        status=result.status,
        message=result.message,
    )


# ------------------------------------------------------------------
# POST /admin/editorial/content/{id}/boost
# ------------------------------------------------------------------


@router.post("/editorial/content/{content_id}/boost", response_model=BoostResponse)
def boost_content(
    content_id: int,
    body: BoostRequest,
    db: Session = Depends(get_db),
):
    """Set editorial boost level (0-3)."""
    repo = EditorialRepository(db)
    item = repo.set_boost(content_id, body.level, actor=ACTOR)
    if not item:
        raise HTTPException(status_code=404, detail="Content not found")

    return BoostResponse(
        content_id=item.id,
        editorial_boost=item.editorial_boost,
        message=f"Boost set to {body.level}",
    )


# ------------------------------------------------------------------
# POST /admin/editorial/content/{id}/suppress
# ------------------------------------------------------------------


@router.post("/editorial/content/{content_id}/suppress", response_model=SuppressResponse)
def suppress_content(
    content_id: int,
    db: Session = Depends(get_db),
):
    """Suppress content (soft-delete). Removes from public feeds."""
    repo = EditorialRepository(db)
    item = repo.suppress(content_id, actor=ACTOR)
    if not item:
        raise HTTPException(status_code=404, detail="Content not found")

    return SuppressResponse(
        content_id=item.id,
        suppressed=True,
        message="Content suppressed",
    )


# ------------------------------------------------------------------
# POST /admin/editorial/content/{id}/unsuppress
# ------------------------------------------------------------------


@router.post("/editorial/content/{content_id}/unsuppress", response_model=SuppressResponse)
def unsuppress_content(
    content_id: int,
    db: Session = Depends(get_db),
):
    """Unsuppress content. Restores to public feeds."""
    repo = EditorialRepository(db)
    item = repo.unsuppress(content_id, actor=ACTOR)
    if not item:
        raise HTTPException(status_code=404, detail="Content not found")

    return SuppressResponse(
        content_id=item.id,
        suppressed=False,
        message="Content unsuppressed",
    )
