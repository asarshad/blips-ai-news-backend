"""
User category preferences routes.

Endpoints:
- GET  /users/{device_id}/categories  — Fetch declared category selections
- PUT  /users/{device_id}/categories  — Set (replace) declared category selections

These endpoints are used at onboarding and in the settings screen.
Declared selections feed into the interest_boost component of the
personalised feed score.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.logging import get_logger
from app.repositories.user_repo import UserCategorySelectionRepository

logger = get_logger(__name__)
router = APIRouter()

# Maximum categories a user can select; prevent abuse
MAX_CATEGORIES = 20


class CategorySelectionRequest(BaseModel):
    """Request body for setting category preferences."""

    selected_categories: List[str] = Field(
        ...,
        max_length=MAX_CATEGORIES,
        description="Ordered list of category names the user is interested in.",
        json_schema_extra={"example": ["AI", "Security", "Open Source"]},
    )


class CategorySelectionResponse(BaseModel):
    """Response for category preference endpoints."""

    device_id: str
    selected_categories: List[str]


def get_category_selection_repo(
    db: Session = Depends(get_db),
) -> UserCategorySelectionRepository:
    return UserCategorySelectionRepository(db)


@router.get(
    "/{device_id}/categories",
    response_model=CategorySelectionResponse,
    summary="Get declared category interests",
)
def get_categories(
    device_id: str = Path(..., description="Device identifier"),
    repo: UserCategorySelectionRepository = Depends(get_category_selection_repo),
) -> CategorySelectionResponse:
    """Return the user's declared category selections."""
    categories = repo.get_selected_categories(device_id)
    return CategorySelectionResponse(device_id=device_id, selected_categories=categories)


@router.put(
    "/{device_id}/categories",
    response_model=CategorySelectionResponse,
    summary="Set declared category interests",
)
def set_categories(
    body: CategorySelectionRequest,
    device_id: str = Path(..., description="Device identifier"),
    repo: UserCategorySelectionRepository = Depends(get_category_selection_repo),
) -> CategorySelectionResponse:
    """
    Create or replace the user's declared category selections.

    Categories are stored as an ordered list.  The first element is treated
    as the highest-priority interest when ranking content.
    """
    # Normalise: strip whitespace, deduplicate while preserving order
    seen = set()
    cleaned: List[str] = []
    for cat in body.selected_categories:
        cat = cat.strip()
        if cat and cat not in seen:
            cleaned.append(cat)
            seen.add(cat)

    if len(cleaned) > MAX_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_CATEGORIES} categories may be selected.",
        )

    row = repo.upsert(device_id=device_id, categories=cleaned)
    logger.info(f"Category selection updated for device={device_id}: {cleaned}")
    return CategorySelectionResponse(
        device_id=row.device_id,
        selected_categories=row.selected_categories,
    )
