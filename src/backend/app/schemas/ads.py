"""Ad-related API schemas."""

from typing import Literal, Optional

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


class AdSurfaceConfig(BaseModel):
    """Per-surface ad settings."""

    enabled: bool = False
    frequency: int = Field(
        default=0,
        ge=0,
        description="Insert one ad every N organic items (0 = disabled).",
    )
    first_slot_after: int = Field(
        default=0,
        ge=0,
        description="Minimum number of organic cards before the first slot becomes eligible.",
    )


class AdsSurfacesConfig(BaseModel):
    """Surface-specific ad settings."""

    articles: AdSurfaceConfig = Field(default_factory=AdSurfaceConfig)
    videos: AdSurfaceConfig = Field(default_factory=AdSurfaceConfig)
    reels: AdSurfaceConfig = Field(default_factory=AdSurfaceConfig)


class AdsRuntimeConfig(BaseModel):
    """Raw operator-configurable runtime ad settings."""

    enabled: bool = False
    provider: Literal["admob_native"] = "admob_native"
    canary_percent: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Percent of devices that are eligible to request ads.",
    )
    config_ttl_seconds: int = Field(
        default=300,
        ge=1,
        description="Client-side cache TTL for config refresh.",
    )
    surfaces: AdsSurfacesConfig = Field(default_factory=AdsSurfacesConfig)


class AdsClientConfig(AdsRuntimeConfig):
    """Effective caller-specific runtime ad settings."""

    eligible: bool = False


class AdSurfaceConfigPatch(BaseModel):
    """Partial per-surface ad config update."""

    enabled: bool | None = None
    frequency: int | None = Field(default=None, ge=0)
    first_slot_after: int | None = Field(default=None, ge=0)

    model_config = {"extra": "forbid"}


class AdsSurfacesConfigPatch(BaseModel):
    """Partial surface config update."""

    articles: AdSurfaceConfigPatch | None = None
    videos: AdSurfaceConfigPatch | None = None
    reels: AdSurfaceConfigPatch | None = None

    model_config = {"extra": "forbid"}


class AdsRuntimeConfigPatch(BaseModel):
    """Partial runtime config update."""

    enabled: bool | None = None
    provider: Literal["admob_native"] | None = None
    canary_percent: int | None = Field(default=None, ge=0, le=100)
    config_ttl_seconds: int | None = Field(default=None, ge=1)
    surfaces: AdsSurfacesConfigPatch | None = None

    model_config = {"extra": "forbid"}


class AdsConfigAdminResponse(BaseModel):
    """Admin response for the raw ads config."""

    ads: AdsRuntimeConfig
    source: str


class AppConfigResponse(BaseModel):
    """Top-level response for GET /config."""

    ads: AdsClientConfig = Field(default_factory=AdsClientConfig)
