"""Push notification API schemas."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class PushMode(str, Enum):
    """Runtime delivery modes for push notifications."""

    disabled = "disabled"
    manual = "manual"
    auto_all = "auto_all"


class PushRuntimeConfig(BaseModel):
    """Operator-controlled runtime push config."""

    enabled: bool = False
    mode: PushMode = PushMode.manual
    config_ttl_seconds: int = Field(default=300, ge=1)


class PushClientConfig(PushRuntimeConfig):
    """Effective client-facing push config."""

    pass


class PushRuntimeConfigPatch(BaseModel):
    """Partial runtime push config patch."""

    enabled: bool | None = None
    mode: PushMode | None = None
    config_ttl_seconds: int | None = Field(default=None, ge=1)

    model_config = {"extra": "forbid"}


class PushConfigAdminResponse(BaseModel):
    """Admin response for runtime push config."""

    push: PushRuntimeConfig
    source: str
    provider_ready: bool


class PushSubscriptionUpsertRequest(BaseModel):
    """Device push token registration request."""

    token: str = Field(min_length=16, max_length=4096)
    platform: Literal["android", "ios"]


class PushSubscriptionDeleteRequest(BaseModel):
    """Device push token removal request."""

    token: str = Field(min_length=16, max_length=4096)


class PushSubscriptionResponse(BaseModel):
    """Push subscription mutation response."""

    success: bool
    active: bool
    token: str
    platform: str | None = None


class PushSendResponse(BaseModel):
    """Push send response for admin/manual or auto delivery."""

    success: bool
    skipped: bool = False
    content_id: int
    mode: str
    audience_count: int
    success_count: int
    failure_count: int
    invalid_token_count: int = 0
    log_id: int | None = None
    message: str
