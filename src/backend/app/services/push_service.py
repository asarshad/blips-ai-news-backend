"""Push notification delivery and subscription management."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_redis
from app.core.logging import get_logger
from app.models.content import ContentItem, ContentStatus, ContentType, UserProfile
from app.models.push import PushSendLog, PushSubscription
from app.schemas.push import PushMode, PushSendResponse
from app.services.content_payloads import (
    effective_content_type_value,
    effective_notification_surface,
)
from app.services.push_config_service import PushConfigService
from app.video_surface_rules import effective_content_type

logger = get_logger(__name__)


class PushNotificationError(RuntimeError):
    """Raised when a push operation cannot proceed."""


@dataclass
class PushDeliveryResult:
    """Result from a provider send attempt."""

    success_count: int
    failure_count: int
    invalid_tokens: list[str]


class PushMessagingClient(Protocol):
    """Messaging client interface used by the push service."""

    @property
    def is_available(self) -> bool: ...

    @property
    def availability_error(self) -> str | None: ...

    def send_multicast(
        self,
        *,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, str],
    ) -> PushDeliveryResult: ...


class FirebasePushMessagingClient:
    """Firebase Admin SDK push transport."""

    def __init__(self) -> None:
        self._availability_error: str | None = None

        try:
            import firebase_admin  # type: ignore[import-not-found]
            from firebase_admin import credentials, messaging  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - exercised via availability guard
            self._firebase_admin = None
            self._credentials = None
            self._messaging = None
            self._availability_error = f"firebase-admin unavailable: {exc}"
            return

        self._firebase_admin = firebase_admin
        self._credentials = credentials
        self._messaging = messaging

    @property
    def is_available(self) -> bool:
        return self.availability_error is None

    @property
    def availability_error(self) -> str | None:
        if self._availability_error is not None:
            return self._availability_error
        if not self._service_account_payload:
            return "Firebase service account is not configured"
        return None

    @property
    def _service_account_payload(self) -> dict[str, Any] | None:
        raw_json = (settings.FIREBASE_SERVICE_ACCOUNT_JSON or "").strip()
        if raw_json:
            try:
                return json.loads(raw_json)
            except json.JSONDecodeError as exc:
                self._availability_error = f"Invalid FIREBASE_SERVICE_ACCOUNT_JSON: {exc}"
                return None

        raw_path = (settings.FIREBASE_SERVICE_ACCOUNT_FILE or "").strip()
        if raw_path:
            try:
                with open(raw_path, encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception as exc:  # pragma: no cover - simple env/file error
                self._availability_error = f"Failed to read FIREBASE_SERVICE_ACCOUNT_FILE: {exc}"
                return None
        return None

    def _get_app(self):
        if self.availability_error is not None:
            raise PushNotificationError(self.availability_error)

        assert self._firebase_admin is not None
        assert self._credentials is not None

        app_name = "blips-push"
        try:
            return self._firebase_admin.get_app(app_name)
        except ValueError:
            payload = self._service_account_payload
            if payload is None:
                raise PushNotificationError(
                    self.availability_error or "Firebase push unavailable"
                ) from None
            credential = self._credentials.Certificate(payload)
            return self._firebase_admin.initialize_app(credential, name=app_name)

    def send_multicast(
        self,
        *,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, str],
    ) -> PushDeliveryResult:
        if not tokens:
            return PushDeliveryResult(success_count=0, failure_count=0, invalid_tokens=[])

        if self.availability_error is not None:
            raise PushNotificationError(self.availability_error)

        assert self._messaging is not None
        message = self._messaging.MulticastMessage(
            notification=self._messaging.Notification(title=title, body=body),
            data=data,
            tokens=tokens,
        )
        response = self._messaging.send_each_for_multicast(message, app=self._get_app())

        invalid_tokens: list[str] = []
        for token, send_response in zip(tokens, response.responses, strict=False):
            if send_response.success:
                continue
            code = getattr(getattr(send_response, "exception", None), "code", None)
            if code in {
                "registration-token-not-registered",
                "invalid-argument",
                "invalid-registration-token",
            }:
                invalid_tokens.append(token)

        return PushDeliveryResult(
            success_count=response.success_count,
            failure_count=response.failure_count,
            invalid_tokens=invalid_tokens,
        )


def create_push_messaging_client() -> PushMessagingClient:
    """Build the default push messaging client."""
    return FirebasePushMessagingClient()


class PushNotificationService:
    """Manage push subscriptions and article/video sends."""

    def __init__(
        self,
        db: Session,
        config_service: PushConfigService | None = None,
        messaging_client: PushMessagingClient | None = None,
    ) -> None:
        self.db = db
        self.config_service = config_service or PushConfigService(redis_client=get_redis())
        self.messaging_client = messaging_client or create_push_messaging_client()

    @property
    def provider_ready(self) -> bool:
        return self.messaging_client.is_available

    @property
    def provider_error(self) -> str | None:
        return self.messaging_client.availability_error

    def upsert_subscription(
        self,
        *,
        device_id: str,
        token: str,
        platform: str,
    ) -> PushSubscription:
        normalized_token = token.strip()
        normalized_platform = platform.strip().lower()
        if not normalized_token:
            raise PushNotificationError("Push token is required")
        if normalized_platform not in {"android", "ios"}:
            raise PushNotificationError("Unsupported push platform")

        profile = self.db.query(UserProfile).filter(UserProfile.device_id == device_id).first()
        if profile is None:
            profile = UserProfile(device_id=device_id)
            self.db.add(profile)
            self.db.flush()

        subscription = (
            self.db.query(PushSubscription)
            .filter(
                PushSubscription.device_id == device_id,
                PushSubscription.token == normalized_token,
            )
            .first()
        )
        now = datetime.utcnow()
        if subscription is None:
            subscription = PushSubscription(
                device_id=device_id,
                token=normalized_token,
                platform=normalized_platform,
                active=True,
                last_seen_at=now,
            )
            self.db.add(subscription)
        else:
            subscription.platform = normalized_platform
            subscription.active = True
            subscription.last_seen_at = now
            subscription.updated_at = now

        self.db.commit()
        self.db.refresh(subscription)
        return subscription

    def delete_subscription(self, *, device_id: str, token: str) -> int:
        normalized_token = token.strip()
        if not normalized_token:
            raise PushNotificationError("Push token is required")

        deleted = (
            self.db.query(PushSubscription)
            .filter(
                PushSubscription.device_id == device_id,
                PushSubscription.token == normalized_token,
            )
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return int(deleted or 0)

    def send_manual(self, *, content_id: int, actor: str = "admin") -> PushSendResponse:
        config, _source = self.config_service.get_raw_config()
        if not config.enabled or config.mode == PushMode.disabled:
            raise PushNotificationError("Push notifications are disabled")
        self._require_provider()

        item = self._require_push_eligible_item(content_id, allow_repeated_manual=True)
        return self._send_item(
            item=item,
            mode=PushMode.manual.value,
            actor=actor,
            auto_dedup_key=None,
        )

    def send_auto_for_content_ids(
        self,
        content_ids: list[int],
        *,
        actor: str = "system:auto_all",
    ) -> list[PushSendResponse]:
        config, _source = self.config_service.get_raw_config()
        if (
            not config.enabled
            or config.mode != PushMode.auto_all
            or not self.messaging_client.is_available
        ):
            return []

        results: list[PushSendResponse] = []
        for content_id in dict.fromkeys(content_ids):
            item = self.db.query(ContentItem).filter(ContentItem.id == content_id).first()
            if item is None or not self._is_push_eligible_item(item):
                continue

            auto_dedup_key = f"auto:{item.id}"
            try:
                results.append(
                    self._send_item(
                        item=item,
                        mode=PushMode.auto_all.value,
                        actor=actor,
                        auto_dedup_key=auto_dedup_key,
                    )
                )
            except PushNotificationError as exc:
                logger.warning("Skipping auto push for content %s: %s", content_id, exc)

        return results

    def _send_item(
        self,
        *,
        item: ContentItem,
        mode: str,
        actor: str,
        auto_dedup_key: str | None,
    ) -> PushSendResponse:
        title = item.title
        body = self._notification_body_for_item(item)
        tokens = self._active_tokens()
        log = PushSendLog(
            content_item_id=item.id,
            mode=mode,
            actor=actor,
            title=title,
            body=body,
            audience_count=len(tokens),
            auto_dedup_key=auto_dedup_key,
        )
        self.db.add(log)

        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            if auto_dedup_key is None:
                raise PushNotificationError("Failed to create push send log") from exc
            return PushSendResponse(
                success=True,
                skipped=True,
                content_id=item.id,
                mode=mode,
                audience_count=0,
                success_count=0,
                failure_count=0,
                invalid_token_count=0,
                message="Automatic push already sent for this content item.",
            )

        if not tokens:
            self.db.commit()
            self.db.refresh(log)
            return PushSendResponse(
                success=True,
                skipped=False,
                content_id=item.id,
                mode=mode,
                audience_count=0,
                success_count=0,
                failure_count=0,
                invalid_token_count=0,
                log_id=log.id,
                message="No active push subscribers were available.",
            )

        try:
            delivery = self.messaging_client.send_multicast(
                tokens=tokens,
                title=title,
                body=body,
                data=self._notification_data_for_item(item),
            )
            log.success_count = delivery.success_count
            log.failure_count = delivery.failure_count
            log.invalid_token_count = len(delivery.invalid_tokens)
            if delivery.invalid_tokens:
                self._delete_tokens(delivery.invalid_tokens)
            self.db.commit()
            self.db.refresh(log)
            return PushSendResponse(
                success=True,
                skipped=False,
                content_id=item.id,
                mode=mode,
                audience_count=log.audience_count,
                success_count=log.success_count,
                failure_count=log.failure_count,
                invalid_token_count=log.invalid_token_count,
                log_id=log.id,
                message="Push notification sent.",
            )
        except Exception as exc:
            log.failure_count = len(tokens)
            log.error_message = str(exc)
            self.db.commit()
            self.db.refresh(log)
            raise PushNotificationError(f"Push delivery failed: {exc}") from exc

    def _require_provider(self) -> None:
        if not self.messaging_client.is_available:
            raise PushNotificationError(
                self.messaging_client.availability_error or "Push unavailable"
            )

    def _require_push_eligible_item(
        self,
        content_id: int,
        *,
        allow_repeated_manual: bool,
    ) -> ContentItem:
        item = self.db.query(ContentItem).filter(ContentItem.id == content_id).first()
        if item is None:
            raise PushNotificationError("Content item not found")
        if not self._is_push_eligible_item(item):
            raise PushNotificationError(
                "Only promoted, non-suppressed articles and videos can be sent as push notifications"
            )
        if not allow_repeated_manual and self._has_auto_send_log(item.id):
            raise PushNotificationError("Automatic push already sent for this content item")
        return item

    def _is_push_eligible_item(self, item: ContentItem) -> bool:
        if item.curation_status != ContentStatus.PROMOTED or item.is_suppressed:
            return False
        return effective_content_type(item) in {ContentType.ARTICLE, ContentType.VIDEO}

    def _has_auto_send_log(self, content_id: int) -> bool:
        return (
            self.db.query(PushSendLog.id)
            .filter(
                PushSendLog.content_item_id == content_id,
                PushSendLog.mode == PushMode.auto_all.value,
            )
            .first()
            is not None
        )

    def _active_tokens(self) -> list[str]:
        rows = (
            self.db.query(PushSubscription.token)
            .filter(PushSubscription.active.is_(True))
            .order_by(PushSubscription.last_seen_at.desc())
            .all()
        )
        return [row[0] for row in rows if isinstance(row[0], str) and row[0].strip()]

    def _delete_tokens(self, tokens: list[str]) -> None:
        if not tokens:
            return
        self.db.query(PushSubscription).filter(PushSubscription.token.in_(tokens)).delete(
            synchronize_session=False
        )

    def _notification_data_for_item(self, item: ContentItem) -> dict[str, str]:
        return {
            "surface": effective_notification_surface(item),
            "contentId": str(item.id),
            "payloadVersion": "1",
            "type": effective_content_type_value(item),
        }

    def _notification_body_for_item(self, item: ContentItem) -> str:
        kind = "article" if effective_content_type(item) == ContentType.ARTICLE else "video"
        source = (item.source or "").strip() or "Blips"
        return f"New {kind} from {source}"
