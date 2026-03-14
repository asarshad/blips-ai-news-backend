"""Public application config endpoint."""

from fastapi import APIRouter, Depends, Header

from app.core.dependencies import get_redis
from app.schemas.ads import AppConfigResponse
from app.services.ad_config_service import AdConfigService

router = APIRouter()


def get_ad_config_service(redis_client=Depends(get_redis)) -> AdConfigService:
    """Provide the runtime ad config service."""
    return AdConfigService(redis_client=redis_client)


@router.get(
    "/config",
    response_model=AppConfigResponse,
    summary="Client configuration",
    description=(
        "Returns current server-side feature flags and ad settings. "
        "Clients should fetch on app start and cache with a reasonable TTL."
    ),
)
def get_app_config(
    x_device_id: str | None = Header(None, alias="X-Device-ID"),
    ad_config_service: AdConfigService = Depends(get_ad_config_service),
) -> AppConfigResponse:
    """Return effective server-side ad settings for the caller."""
    return AppConfigResponse(
        ads=ad_config_service.get_public_config(x_device_id),
    )
