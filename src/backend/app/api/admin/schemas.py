"""
Pydantic schemas for the admin/editorial API.
"""

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class EditorialActionType(str, Enum):
    ADD = "ADD"
    BOOST = "BOOST"
    SUPPRESS = "SUPPRESS"
    UNSUPPRESS = "UNSUPPRESS"


# ---------------------------------------------------------------------------
# Content listing
# ---------------------------------------------------------------------------


class ContentFilter(BaseModel):
    """Query parameters for /admin/editorial/content."""

    day: Optional[date] = None
    content_type: Optional[str] = Field(None, alias="type")
    source: Optional[str] = None
    suppressed: Optional[bool] = None
    manual_added: Optional[bool] = None
    sort_by: str = Field("published_at", pattern="^(published_at|created_at|editorial_boost)$")
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=200)


class ContentItemSummary(BaseModel):
    """Lightweight representation for list endpoints."""

    id: int
    title: str
    source: str
    source_url: str
    canonical_url: Optional[str] = None
    content_type: str
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    suppressed: bool = False
    editorial_boost: int = 0
    manual_added: bool = False
    quality_score: Optional[float] = None
    cluster_id: Optional[str] = None
    freshness_tier: Optional[str] = None
    global_score: Optional[float] = None

    class Config:
        from_attributes = True


class PaginatedContentResponse(BaseModel):
    items: List[ContentItemSummary]
    total: int
    page: int
    page_size: int
    pages: int


# ---------------------------------------------------------------------------
# Content detail
# ---------------------------------------------------------------------------


class EditorialActionRecord(BaseModel):
    id: int
    action_type: str
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
    actor: str
    created_at: datetime

    class Config:
        from_attributes = True


class ContentItemDetail(ContentItemSummary):
    description: Optional[str] = None
    summary: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    topics: Optional[List[str]] = None
    entities: Optional[List[Any]] = None
    ai_processed: bool = False
    dedupe_key: Optional[str] = None
    canonical_key: Optional[str] = None
    trend_score: Optional[float] = None
    recency_score: Optional[float] = None
    diversity_boost: Optional[float] = None
    added_by: Optional[str] = None
    added_at: Optional[datetime] = None
    last_modified_by: Optional[str] = None
    last_modified_at: Optional[datetime] = None
    recent_actions: List[EditorialActionRecord] = []


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


class SubmitURLRequest(BaseModel):
    url: str = Field(..., min_length=5, max_length=2048)
    importance_level: int = Field(0, ge=0, le=3)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class SubmitURLResponse(BaseModel):
    content_id: Optional[int] = None
    duplicate: bool = False
    status: str  # "created" | "duplicate_boosted" | "duplicate_exists" | "queued"
    message: str


# ---------------------------------------------------------------------------
# Boost / Suppress
# ---------------------------------------------------------------------------


class BoostRequest(BaseModel):
    level: int = Field(..., ge=0, le=3)


class SuppressResponse(BaseModel):
    content_id: int
    suppressed: bool
    message: str


class BoostResponse(BaseModel):
    content_id: int
    editorial_boost: int
    message: str
