"""
Pydantic schemas for analytics events (impression / click).
"""

from typing import Optional

from pydantic import BaseModel, Field


class EventPayload(BaseModel):
    """Shared payload for impression and click events."""

    item_type: str = Field(
        ...,
        description="Discriminator: 'ARTICLE', 'VIDEO', 'REEL', or 'AD'.",
    )
    content_id: Optional[int] = Field(
        default=None,
        description="Organic content ID (mutually exclusive with ad_id).",
    )
    ad_id: Optional[str] = Field(
        default=None,
        description="Ad identifier (mutually exclusive with content_id).",
    )
    timestamp: Optional[str] = Field(
        default=None,
        description="Client-side ISO-8601 timestamp.",
    )
    surface: str = Field(
        ...,
        description="Where the event happened: 'feed', 'video', 'reel'.",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Anonymous session identifier (no PII).",
    )
    provider: Optional[str] = Field(
        default=None,
        description="Ads provider for ad events, e.g. 'admob_native'.",
    )
    ad_unit_id: Optional[str] = Field(
        default=None,
        description="AdMob ad unit identifier for the rendered ad slot.",
    )
    slot_index: Optional[int] = Field(
        default=None,
        ge=0,
        description="Zero-based ad slot index within the current feed surface.",
    )
    load_status: Optional[str] = Field(
        default=None,
        description="Load result for ad lifecycle events, e.g. 'loaded' or 'failed'.",
    )


class EventResponse(BaseModel):
    """Acknowledgement returned to the client."""

    status: str = "ok"
