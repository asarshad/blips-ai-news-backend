"""
Public application config endpoint.

Returns feature flags and ad configuration that clients can cache
on startup.  No authentication required — the payload is non-secret.
"""

from fastapi import APIRouter

from app.core.config import settings
from app.schemas.ads import AdsConfig, AppConfigResponse

router = APIRouter()


@router.get(
    "/config",
    response_model=AppConfigResponse,
    summary="Client configuration",
    description=(
        "Returns current server-side feature flags and ad settings. "
        "Clients should fetch on app start and cache with a reasonable TTL."
    ),
)
def get_app_config() -> AppConfigResponse:
    """Return server-side feature flags and ad settings."""
    return AppConfigResponse(
        ads=AdsConfig(
            ads_enabled=settings.ADS_ENABLED,
            ads_feed_card_enabled=settings.ADS_FEED_CARD_ENABLED,
            ads_banner_enabled=settings.ADS_BANNER_ENABLED,
            ads_feed_frequency=settings.ADS_FEED_FREQUENCY,
            ads_canary_percent=settings.ADS_CANARY_PERCENT,
        ),
    )
