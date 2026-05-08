from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.content import ContentItem
from app.models.content_event import ContentEventOutbox
from app.models.source_fetch_state import SourceFetchState
from app.services import alerting_service
from app.services.major_news_constants import MAJOR_NEWS_DISCOVERED_VIA, MAJOR_NEWS_SOURCE_TYPE
from app.services.strategic_content_health_service import (
    compute_strategic_content_health,
    emit_strategic_content_alerts,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


@compiles(PgEnum, "sqlite")
def _compile_enum_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


def _session():
    engine = create_engine("sqlite:///:memory:")
    for table in (ContentItem.__table__, ContentEventOutbox.__table__, SourceFetchState.__table__):
        table.create(bind=engine)
    return sessionmaker(bind=engine)()


def test_strategic_health_flags_ready_stall_and_major_news_stuck(monkeypatch):
    db = _session()
    now = datetime(2026, 5, 3, 20, 0, 0, tzinfo=timezone.utc)
    old = now - timedelta(hours=10)

    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_ALERT_READY_STALL_HOURS",
        6,
    )
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_ALERT_SURFACE_STALL_HOURS",
        12,
    )
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_ALERT_MAJOR_NEWS_STUCK_MINUTES",
        45,
    )
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_ALERT_MAJOR_NEWS_STUCK_MIN_COUNT",
        1,
    )

    db.add(
        ContentItem(
            type="ARTICLE",
            source="CNBC Technology",
            source_url="https://example.com/major",
            published_at=old.replace(tzinfo=None),
            created_at=(now - timedelta(hours=2)).replace(tzinfo=None),
            title="Major AI policy story",
            curation_status="PROMOTED",
            readiness_status="PENDING",
            readiness_reason="awaiting_ai_processing",
            readiness_updated_at=(now - timedelta(hours=2)).replace(tzinfo=None),
            is_major_tech_news=True,
            discovered_via=MAJOR_NEWS_DISCOVERED_VIA,
            is_suppressed=False,
        )
    )
    db.add(
        SourceFetchState(
            source_type=MAJOR_NEWS_SOURCE_TYPE,
            feed_name="CNBC Technology",
            source_url="https://example.com/rss",
            health_status="healthy",
            updated_at=(now - timedelta(minutes=20)).replace(tzinfo=None),
        )
    )
    db.commit()

    health = compute_strategic_content_health(db, now=now)
    issue_keys = {issue["key"] for issue in health["issues"]}

    assert health["status"] == "critical"
    assert "all_surfaces_ready_stalled" in issue_keys
    assert "major_news_stuck_pending" in issue_keys


def test_strategic_health_reports_large_event_backlog(monkeypatch):
    db = _session()
    now = datetime(2026, 5, 3, 20, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_ALERT_EVENT_BACKLOG_COUNT",
        2,
    )
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_ALERT_EVENT_BACKLOG_MINUTES",
        30,
    )
    db.add_all(
        [
            ContentEventOutbox(
                content_item_id=1,
                event_type="content.ai_summary.requested",
                payload={"content_id": 1},
                status="pending",
                available_at=(now - timedelta(hours=2)).replace(tzinfo=None),
            ),
            ContentEventOutbox(
                content_item_id=2,
                event_type="content.ai_summary.requested",
                payload={"content_id": 2},
                status="pending",
                available_at=(now - timedelta(hours=1)).replace(tzinfo=None),
            ),
        ]
    )
    db.commit()

    health = compute_strategic_content_health(db, now=now)

    assert "content_event_backlog" in {issue["key"] for issue in health["issues"]}
    assert health["event_backlog"]["due_count"] == 2


def test_strategic_health_reports_worker_memory_pressure(monkeypatch):
    db = _session()
    now = datetime(2026, 5, 3, 20, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_ALERT_WORKER_MEMORY_THROTTLED_LANES",
        2,
    )
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_ALERT_WORKER_MEMORY_THROTTLED_MINUTES",
        20,
    )
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.read_lane_heartbeats",
        lambda: {
            "available": True,
            "lanes": [
                {
                    "lane": "ai_summary",
                    "status": "throttled",
                    "memory_mb": 1500.0,
                    "soft_limit_mb": 1400,
                    "last_throttle_reason": "memory_pressure",
                    "throttle_started_at": (now - timedelta(minutes=25)).isoformat(),
                    "updated_at": now.isoformat(),
                },
                {
                    "lane": "article_images",
                    "status": "idle",
                    "is_memory_throttled": True,
                    "memory_mb": 1500.0,
                    "soft_limit_mb": 1400,
                    "throttle_started_at": (now - timedelta(minutes=21)).isoformat(),
                    "updated_at": now.isoformat(),
                },
            ],
        },
    )

    health = compute_strategic_content_health(db, now=now)

    assert "worker_memory_pressure" in {issue["key"] for issue in health["issues"]}
    assert health["worker_memory_pressure"]["throttled_lane_count"] == 2
    assert health["worker_memory_pressure"]["sustained_throttled_lane_count"] == 2
    assert [row["lane"] for row in health["memory_throttled_lanes"]] == [
        "ai_summary",
        "article_images",
    ]


def test_strategic_health_does_not_alert_for_unsustained_memory_pressure(monkeypatch):
    db = _session()
    now = datetime(2026, 5, 3, 20, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_ALERT_WORKER_MEMORY_THROTTLED_LANES",
        2,
    )
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_ALERT_WORKER_MEMORY_THROTTLED_MINUTES",
        20,
    )
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.read_lane_heartbeats",
        lambda: {
            "available": True,
            "lanes": [
                {
                    "lane": "ai_summary",
                    "status": "throttled",
                    "throttle_started_at": (now - timedelta(minutes=25)).isoformat(),
                },
                {
                    "lane": "article_images",
                    "status": "throttled",
                    "throttle_started_at": (now - timedelta(minutes=3)).isoformat(),
                },
            ],
        },
    )

    health = compute_strategic_content_health(db, now=now)

    assert "worker_memory_pressure" not in {issue["key"] for issue in health["issues"]}
    assert health["worker_memory_pressure"]["sustained_throttled_lane_count"] == 1


def test_emit_strategic_content_alerts_sends_deduped_issue(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.strategic_content_health_service.settings.STRATEGIC_CONTENT_ALERTS_ENABLED",
        True,
    )
    monkeypatch.setattr(
        alerting_service,
        "send_alert",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    health = {
        "issues": [
            {
                "key": "all_surfaces_ready_stalled",
                "severity": "critical",
                "message": "No app-ready content produced",
                "context": {"window_hours": 6},
            }
        ]
    }

    assert emit_strategic_content_alerts(health) == 1
    assert calls[0]["alert_key"] == "strategic_content_all_surfaces_ready_stalled"


def test_discord_alert_payload_uses_native_discord_shape(monkeypatch):
    posted = {}

    class _Response:
        status_code = 204
        text = ""

    def _post(url, *, json, timeout, headers):  # noqa: A002
        posted["url"] = url
        posted["json"] = json
        posted["timeout"] = timeout
        posted["headers"] = headers
        return _Response()

    monkeypatch.setattr(alerting_service.settings, "ALERT_ENABLED", True)
    monkeypatch.setattr(alerting_service.settings, "ALERT_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test")
    monkeypatch.setattr(alerting_service.requests, "post", _post)
    monkeypatch.setattr(alerting_service, "_check_rate_limit", lambda *_args: True)

    sent = alerting_service.send_alert(
        alerting_service.AlertSeverity.CRITICAL,
        "Pipeline stalled",
        {"window_hours": 6},
        alert_key="test",
    )

    assert sent is True
    assert posted["json"]["embeds"][0]["title"] == "[CRITICAL] Pipeline stalled"
    assert posted["json"]["content"].endswith("**Blips Alert**")
