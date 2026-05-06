"""Authenticated application config endpoint."""

from fastapi import APIRouter, Depends

from app.core.config import settings
from app.core.dependencies import get_redis
from app.core.session_auth import AuthenticatedSession, require_session_token
from app.schemas.ads import AppConfigResponse
from app.services.ad_config_service import AdConfigService
from app.services.push_config_service import PushConfigService
from app.services.push_service import create_push_messaging_client

router = APIRouter()


def get_ad_config_service(redis_client=Depends(get_redis)) -> AdConfigService:
    """Provide the runtime ad config service."""
    return AdConfigService(redis_client=redis_client)


def get_push_config_service(redis_client=Depends(get_redis)) -> PushConfigService:
    """Provide the runtime push config service."""
    return PushConfigService(redis_client=redis_client)


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
    session: AuthenticatedSession = Depends(require_session_token),
    ad_config_service: AdConfigService = Depends(get_ad_config_service),
    push_config_service: PushConfigService = Depends(get_push_config_service),
) -> AppConfigResponse:
    """Return effective server-side ad settings for the caller."""
    push_messaging_client = create_push_messaging_client()
    return AppConfigResponse(
        ads=ad_config_service.get_public_config(session.device_id),
        push=push_config_service.get_public_config(
            provider_ready=push_messaging_client.is_available,
        ),
        min_recommended_version=settings.MIN_RECOMMENDED_VERSION,
        ios_app_store_url=settings.IOS_APP_STORE_URL,
        android_play_store_url=settings.ANDROID_PLAY_STORE_URL,
    )
