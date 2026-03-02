"""
Ad-related Pydantic schemas (contract only — no SDK).

Defines the data contract for synthetic ad items that may later be
injected into feed responses.  The backend never calls an ad network;
these schemas exist purely so that the API contract is stable before
any provider is integrated.
"""

from typing import Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Ad tracking / metadata sub-models
# ---------------------------------------------------------------------------

class AdTracking(BaseModel):
    """Optional pixel/beacon URLs for impression and click attribution."""

    impression_url: Optional[str] = None
    click_url: Optional[str] = None


class AdMetadata(BaseModel):
    """Opaque campaign-level metadata for use by future ad providers."""

    campaign_id: Optional[str] = None
    priority: Optional[int] = None


# ---------------------------------------------------------------------------
# Core ad payload
# ---------------------------------------------------------------------------

class AdItem(BaseModel):
    """A single ad slot payload returned as part of a feed response.

    When ads are enabled the feed mixer MAY inject ``AdItem`` objects
    alongside organic ``ARTICLE`` / ``VIDEO`` / ``REEL`` items.
    """

    item_type: str = Field(default="AD", description="Discriminator — always 'AD'.")
    ad_id: str = Field(..., description="Unique ad identifier.")
    placement_id: str = Field(
        ...,
        description="Surface identifier, e.g. 'feed_fullpage', 'banner_bottom'.",
    )
    title: Optional[str] = None
    body: Optional[str] = None
    image_url: Optional[str] = None
    click_url: Optional[str] = None
    sponsor_name: str = Field(..., description="Sponsor display name.")
    label: str = Field(default="Sponsored", description="Badge text — always 'Sponsored'.")
    tracking: Optional[AdTracking] = None
    metadata: Optional[AdMetadata] = None


# ---------------------------------------------------------------------------
# Server-side ad configuration (returned by GET /config)
# ---------------------------------------------------------------------------

class AdsConfig(BaseModel):
    """Server-side ad feature flag bundle.

    All flags default to ``False`` / ``0``, ensuring ads are
    completely disabled unless the operator explicitly enables them.
    """

    ads_enabled: bool = False
    ads_feed_card_enabled: bool = False
    ads_banner_enabled: bool = False
    ads_feed_frequency: int = Field(
        default=0,
        description="Insert one ad every N organic items (0 = disabled).",
    )
    ads_canary_percent: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Percent of requests that receive ads (for gradual rollout).",
    )


class AppConfigResponse(BaseModel):
    """Top-level response for GET /config."""

    ads: AdsConfig = Field(default_factory=AdsConfig)
