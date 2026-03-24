"""Admin-only share-target routes for phone shortcut submissions."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin.schemas import SubmitURLRequest, SubmitURLResponse
from app.core.auth import require_admin_share_token
from app.core.dependencies import get_db
from app.domain.editorial.service import EditorialService

router = APIRouter(
    prefix="/admin/share-target",
    tags=["admin-share-target"],
)


@router.post("/submit", response_model=SubmitURLResponse)
def submit_share_target_url(
    body: SubmitURLRequest,
    db: Session = Depends(get_db),
    _share_token: str = Depends(require_admin_share_token),
):
    """Accept a phone share-sheet submission using the scoped admin share token."""
    service = EditorialService(db)
    result = service.submit_url(
        url=body.url,
        importance_level=body.importance_level,
        actor="ios_shortcut",
    )
    return SubmitURLResponse(
        content_id=result.content_id,
        duplicate=result.duplicate,
        status=result.status,
        message=result.message,
        content_type=result.content_type,
    )
