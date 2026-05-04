"""
Alerting Service.

Provides webhook-based alerting for production monitoring.
Supports Slack, Discord, or generic webhooks.

Features:
- Rate limiting: max 1 alert per key per 5 minutes
- Severity levels: info, warning, critical
- Context enrichment: includes service name, timestamp
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Process boot time — used for warm-up grace after deploy/restart.
_PROCESS_BOOT_AT = datetime.now(timezone.utc)


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# Severity to Slack color mapping
SEVERITY_COLORS = {
    AlertSeverity.INFO: "#36a64f",  # Green
    AlertSeverity.WARNING: "#ffcc00",  # Yellow
    AlertSeverity.CRITICAL: "#ff0000",  # Red
}

# Severity to emoji mapping
SEVERITY_EMOJI = {
    AlertSeverity.INFO: "ℹ️",
    AlertSeverity.WARNING: "⚠️",
    AlertSeverity.CRITICAL: "🚨",
}


def _get_redis_client():
    """Get Redis client for rate limiting."""
    try:
        from app.core.dependencies import get_redis

        return get_redis()
    except Exception as e:
        logger.debug(f"Redis not available for alert rate limiting: {e}")
        return None


_SEVERITY_RANK = {
    AlertSeverity.INFO: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.CRITICAL: 2,
}


def _floor_severity() -> AlertSeverity:
    raw = str(getattr(settings, "ALERT_MIN_SEVERITY", "warning") or "warning").lower()
    try:
        return AlertSeverity(raw)
    except ValueError:
        return AlertSeverity.WARNING


def _meets_severity_floor(severity: AlertSeverity) -> bool:
    return _SEVERITY_RANK[severity] >= _SEVERITY_RANK[_floor_severity()]


def _cooldown_seconds(severity: AlertSeverity) -> int:
    if severity == AlertSeverity.CRITICAL:
        return int(getattr(settings, "ALERT_COOLDOWN_CRITICAL_SECONDS", 300) or 300)
    return int(getattr(settings, "ALERT_COOLDOWN_WARNING_SECONDS", 86400) or 86400)


def _in_warmup(severity: AlertSeverity) -> bool:
    if severity == AlertSeverity.CRITICAL:
        return False
    grace = int(getattr(settings, "ALERT_WARMUP_GRACE_SECONDS", 7200) or 0)
    if grace <= 0:
        return False
    return (datetime.now(timezone.utc) - _PROCESS_BOOT_AT).total_seconds() < grace


def _in_quiet_hours(severity: AlertSeverity) -> bool:
    if severity == AlertSeverity.CRITICAL:
        return False
    spec = str(getattr(settings, "ALERT_QUIET_HOURS_UTC", "") or "").strip()
    if "-" not in spec:
        return False
    try:
        start_str, end_str = spec.split("-", 1)
        start = int(start_str)
        end = int(end_str)
    except ValueError:
        return False
    if not (0 <= start <= 23 and 0 <= end <= 23):
        return False
    hour = datetime.now(timezone.utc).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Wraps midnight (e.g. 22-13 means 22:00..23:59 + 00:00..12:59)
    return hour >= start or hour < end


def _check_rate_limit(alert_key: str, severity: AlertSeverity) -> bool:
    """
    Check if alert should be sent. The cooldown TTL doubles as an
    edge-trigger latch when paired with clear_alert_state().

    Returns:
        True if alert should be sent, False if rate limited
    """
    redis_client = _get_redis_client()
    if not redis_client:
        return True

    try:
        cache_key = f"blips:alert_cooldown:{alert_key}"
        if redis_client.exists(cache_key):
            logger.debug("Alert rate limited: %s", alert_key)
            return False

        redis_client.setex(cache_key, _cooldown_seconds(severity), "1")
        return True

    except Exception as e:
        logger.warning(f"Rate limit check failed: {e}")
        return True  # On error, allow alert


def clear_alert_state(alert_key: str) -> bool:
    """
    Clear the cooldown latch for an alert key. Call this when the underlying
    condition has resolved, so the next occurrence will alert immediately
    instead of waiting for the cooldown TTL to expire.
    """
    redis_client = _get_redis_client()
    if not redis_client:
        return False
    try:
        deleted = redis_client.delete(f"blips:alert_cooldown:{alert_key}")
        return bool(deleted)
    except Exception as e:  # noqa: BLE001
        logger.debug("clear_alert_state failed for %s: %s", alert_key, e)
        return False


def _format_slack_payload(
    severity: AlertSeverity,
    message: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Format alert as Slack-compatible webhook payload.

    Args:
        severity: Alert severity level
        message: Main alert message
        context: Additional context dict

    Returns:
        Slack webhook payload dict
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    emoji = SEVERITY_EMOJI.get(severity, "📢")
    color = SEVERITY_COLORS.get(severity, "#808080")

    # Build context fields
    fields = [
        {"title": "Service", "value": settings.PROJECT_NAME, "short": True},
        {"title": "Severity", "value": severity.value.upper(), "short": True},
        {"title": "Timestamp", "value": timestamp, "short": False},
    ]

    if context:
        for key, value in context.items():
            fields.append(
                {
                    "title": key.replace("_", " ").title(),
                    "value": str(value),
                    "short": True,
                }
            )

    return {
        "attachments": [
            {
                "color": color,
                "fallback": f"{emoji} [{severity.value.upper()}] {message}",
                "pretext": f"{emoji} *Blips Alert*",
                "text": message,
                "fields": fields,
                "footer": "Blips Monitoring",
                "ts": int(datetime.now(timezone.utc).timestamp()),
            }
        ]
    }


def _is_discord_webhook(webhook_url: str) -> bool:
    host = urlparse(webhook_url or "").netloc.lower()
    return "discord.com" in host or "discordapp.com" in host


def _format_discord_payload(
    severity: AlertSeverity,
    message: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Format alert as a native Discord webhook payload."""
    timestamp = datetime.now(timezone.utc).isoformat()
    emoji = SEVERITY_EMOJI.get(severity, "")
    color = int(SEVERITY_COLORS.get(severity, "#808080").lstrip("#"), 16)
    fields = [
        {"name": "Service", "value": settings.PROJECT_NAME, "inline": True},
        {"name": "Severity", "value": severity.value.upper(), "inline": True},
        {"name": "Timestamp", "value": timestamp, "inline": False},
    ]
    if context:
        for key, value in context.items():
            fields.append(
                {
                    "name": key.replace("_", " ").title(),
                    "value": str(value)[:1024] or "—",
                    "inline": True,
                }
            )

    return {
        "content": f"{emoji} **Blips Alert**",
        "embeds": [
            {
                "title": f"[{severity.value.upper()}] {message}",
                "color": color,
                "fields": fields[:25],
                "footer": {"text": "Blips Monitoring"},
                "timestamp": timestamp,
            }
        ],
    }


def send_alert(
    severity: AlertSeverity,
    message: str,
    context: Optional[Dict[str, Any]] = None,
    alert_key: Optional[str] = None,
    bypass_rate_limit: bool = False,
) -> bool:
    """
    Send alert to configured webhook.

    Args:
        severity: Alert severity level
        message: Main alert message
        context: Additional context dict
        alert_key: Unique key for rate limiting (default: message hash)
        bypass_rate_limit: Skip rate limit check

    Returns:
        True if alert sent successfully, False otherwise
    """
    # Check if alerting is enabled
    if not getattr(settings, "ALERT_ENABLED", False):
        logger.debug("Alerting disabled via ALERT_ENABLED=false")
        return False

    if not _meets_severity_floor(severity):
        logger.debug(
            "Alert below severity floor (%s): [%s] %s",
            _floor_severity().value,
            severity.value,
            message,
        )
        return False

    if _in_warmup(severity):
        logger.info(
            "Alert suppressed during warm-up grace: [%s] %s",
            severity.value,
            message,
        )
        return False

    if _in_quiet_hours(severity):
        logger.info(
            "Alert suppressed during quiet hours: [%s] %s",
            severity.value,
            message,
        )
        return False

    webhook_url = (
        getattr(settings, "ALERT_DISCORD_WEBHOOK_URL", "")
        or getattr(settings, "ALERT_WEBHOOK_URL", "")
    )
    if not webhook_url:
        logger.debug("No ALERT_WEBHOOK_URL configured")
        return False

    # Rate limiting
    if not bypass_rate_limit:
        rate_key = alert_key or f"{severity.value}:{hash(message)}"
        if not _check_rate_limit(rate_key, severity):
            return False

    try:
        payload = (
            _format_discord_payload(severity, message, context)
            if _is_discord_webhook(webhook_url)
            else _format_slack_payload(severity, message, context)
        )

        # Fire in a daemon thread so a slow or unreachable Discord endpoint
        # cannot block the maintenance-lane task that called us.
        import threading

        result: dict = {}

        def _send() -> None:
            try:
                r = requests.post(
                    webhook_url,
                    json=payload,
                    timeout=8,
                    headers={"Content-Type": "application/json"},
                )
                result["status_code"] = r.status_code
                result["ok"] = 200 <= r.status_code < 300
                if not result["ok"]:
                    logger.warning("Alert webhook returned %s: %s", r.status_code, r.text[:200])
            except requests.RequestException as exc:
                result["ok"] = False
                logger.error("Failed to send alert: %s", exc)

        t = threading.Thread(target=_send, daemon=True)
        t.start()
        t.join(timeout=9)  # wait at most 9 s; daemon thread won't block process exit

        if result.get("ok"):
            logger.info("Alert sent: [%s] %s", severity.value, message)
            return True
        if not result:
            logger.warning("Alert webhook timed out for [%s] %s", severity.value, message)
        return False

    except Exception as e:  # noqa: BLE001
        logger.error("Failed to send alert: %s", e)
        return False


def alert_health_check_failed(
    database_status: str,
    redis_status: str,
    details: Optional[str] = None,
) -> bool:
    """
    Send alert for health check failure.

    Args:
        database_status: DB health status ("ok" or error message)
        redis_status: Redis health status ("ok" or error message)
        details: Additional failure details

    Returns:
        True if alert sent
    """
    message = "Health check failing - service may be degraded"

    context = {
        "database": database_status,
        "redis": redis_status,
    }

    if details:
        context["details"] = details

    return send_alert(
        severity=AlertSeverity.CRITICAL,
        message=message,
        context=context,
        alert_key="health_check_failed",
    )


def alert_ingestion_stalled(
    last_success_at: Optional[datetime],
    hours_since_ingestion: float,
) -> bool:
    """
    Send alert for ingestion stall.

    Args:
        last_success_at: Timestamp of last successful ingestion
        hours_since_ingestion: Hours since last ingestion

    Returns:
        True if alert sent
    """
    message = f"Ingestion stalled - no new content for {hours_since_ingestion:.1f} hours"

    context = {
        "last_success": last_success_at.isoformat() if last_success_at else "never",
        "hours_stalled": f"{hours_since_ingestion:.1f}h",
    }

    return send_alert(
        severity=AlertSeverity.CRITICAL,
        message=message,
        context=context,
        alert_key="ingestion_stalled",
    )


def alert_low_inventory(
    surface: str,
    tier_a_count: int,
    threshold: int,
) -> bool:
    """
    Send alert for low content inventory.

    Args:
        surface: Content surface (articles, videos, reels)
        tier_a_count: Current Tier A item count
        threshold: Minimum threshold

    Returns:
        True if alert sent
    """
    message = f"Low inventory: {surface} Tier A below threshold"

    context = {
        "surface": surface,
        "tier_a_count": tier_a_count,
        "threshold": threshold,
    }

    return send_alert(
        severity=AlertSeverity.WARNING,
        message=message,
        context=context,
        alert_key=f"low_inventory_{surface}",
    )


def alert_inventory_surface_degraded(
    *,
    surface: str,
    issues: list[str],
    recent_refresh_count: int | None = None,
    recent_refresh_threshold: int | None = None,
    newest_item_age_seconds: int | None = None,
) -> bool:
    """Send alert for an operationally degraded inventory surface."""
    message = f"Inventory degraded: {surface} surface below freshness guardrails"
    context: Dict[str, Any] = {
        "surface": surface,
        "issues": "; ".join(issues[:3]) if issues else "unknown",
    }
    if recent_refresh_count is not None:
        context["recent_refresh_count"] = recent_refresh_count
    if recent_refresh_threshold is not None:
        context["recent_refresh_threshold"] = recent_refresh_threshold
    if newest_item_age_seconds is not None:
        context["newest_item_age_seconds"] = newest_item_age_seconds

    return send_alert(
        severity=AlertSeverity.WARNING,
        message=message,
        context=context,
        alert_key=f"inventory_degraded_{surface}",
    )


def alert_scheduler_job_issue(
    *,
    job_id: str,
    issue_type: str,
    details: str | None = None,
    severity: AlertSeverity = AlertSeverity.WARNING,
) -> bool:
    """Send alert for scheduler overlaps, misses, or job failures."""
    message = f"Scheduler issue: {job_id} ({issue_type})"
    context: Dict[str, Any] = {
        "job_id": job_id,
        "issue_type": issue_type,
    }
    if details:
        context["details"] = details[:500]

    return send_alert(
        severity=severity,
        message=message,
        context=context,
        alert_key=f"scheduler_{job_id}_{issue_type}",
    )


def alert_strategic_content_issue(
    *,
    issue_key: str,
    message: str,
    severity: AlertSeverity,
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """Send an alert for app-level content freshness/supply risk."""
    return send_alert(
        severity=severity,
        message=message,
        context=context or {},
        alert_key=f"strategic_content_{issue_key}",
    )


def alert_llm_quota_exceeded(
    daily_spend: float,
    ceiling: float,
    provider: str,
) -> bool:
    """
    Send alert for LLM quota exhaustion.

    Args:
        daily_spend: Amount spent today (USD)
        ceiling: Daily ceiling (USD)
        provider: LLM provider name

    Returns:
        True if alert sent
    """
    message = "LLM daily quota exceeded - AI features degraded"

    context = {
        "provider": provider,
        "daily_spend": f"${daily_spend:.2f}",
        "ceiling": f"${ceiling:.2f}",
    }

    return send_alert(
        severity=AlertSeverity.WARNING,
        message=message,
        context=context,
        alert_key="llm_quota_exceeded",
    )
