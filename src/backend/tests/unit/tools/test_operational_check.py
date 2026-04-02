"""Unit tests for the operational probe helper."""

import urllib.error

from scripts import operational_check
from scripts.operational_check import (
    _normalize_surface_set,
    _unexpected_unhealthy_surfaces,
    evaluate_report_failures,
)


def test_evaluate_report_failures_ignores_clean_report_by_default():
    failures = evaluate_report_failures(
        {
            "anomalies": [],
            "admin": {
                "ops_status": {"status_code": 200},
            },
        },
        fail_on_anomaly=False,
        require_admin_success=False,
    )

    assert failures == []


def test_evaluate_report_failures_surfaces_anomalies_when_enabled():
    failures = evaluate_report_failures(
        {
            "anomalies": ["inventory unhealthy (reels)"],
            "admin": {"skipped": True},
        },
        fail_on_anomaly=True,
        require_admin_success=False,
    )

    assert failures == ["inventory unhealthy (reels)"]


def test_evaluate_report_failures_requires_admin_checks_when_requested():
    failures = evaluate_report_failures(
        {
            "anomalies": [],
            "admin": {"skipped": True},
        },
        fail_on_anomaly=False,
        require_admin_success=True,
    )

    assert failures == ["admin checks skipped"]


def test_evaluate_report_failures_flags_non_200_admin_results():
    failures = evaluate_report_failures(
        {
            "anomalies": [],
            "admin": {
                "ops_status": {"status_code": 200},
                "source_metrics": {"status_code": 503},
            },
        },
        fail_on_anomaly=False,
        require_admin_success=True,
    )

    assert failures == ["admin check failed: source_metrics"]


def test_normalize_surface_set_lowercases_and_deduplicates():
    assert _normalize_surface_set(["Reels", " reels ", "VIDEOS"]) == {
        "reels",
        "videos",
    }


def test_unexpected_unhealthy_surfaces_respects_allowed_surfaces():
    unexpected = _unexpected_unhealthy_surfaces(
        {
            "surfaces": {
                "articles": {"is_healthy": True},
                "videos": {"is_healthy": False},
                "reels": {"is_healthy": False},
            },
        },
        allowed_surfaces={"reels"},
    )

    assert unexpected == ["videos"]


def test_main_returns_nonzero_when_fail_on_anomaly_enabled(monkeypatch, capsys):
    monkeypatch.setattr(
        operational_check,
        "run",
        lambda *_args, **_kwargs: {
            "anomalies": ["articles_recent: empty item list"],
            "admin": {"skipped": True},
        },
    )

    exit_code = operational_check.main(
        ["--base-url", "https://example.com", "--fail-on-anomaly"]
    )

    assert exit_code == 1
    assert "FAIL: articles_recent: empty item list" in capsys.readouterr().err


def test_main_requires_admin_success_when_requested(monkeypatch, capsys):
    monkeypatch.setattr(
        operational_check,
        "run",
        lambda *_args, **_kwargs: {
            "anomalies": [],
            "admin": {"skipped": True},
        },
    )

    exit_code = operational_check.main(
        ["--base-url", "https://example.com", "--require-admin-success"]
    )

    assert exit_code == 1
    assert "FAIL: admin checks skipped" in capsys.readouterr().err


def test_run_flags_degraded_health_as_anomaly(monkeypatch):
    responses = iter(
        [
            (
                200,
                {"content-type": "application/json"},
                {
                    "status": "degraded",
                    "checks": {
                        "scheduler": {
                            "detail": "Ingestion is expected externally but no scheduler leader lock is visible."
                        }
                    },
                },
            ),
            (200, {"content-type": "application/json"}, {"is_healthy": True, "surfaces": {}}),
            (
                200,
                {"content-type": "application/json"},
                {"access_token": "token", "device_id": "abc", "expires_in": 3600},
            ),
            (200, {"x-cache": "MISS", "x-feed-source": "db", "x-feed-version": "1"}, {"items": [{"id": 1}]}),
            (200, {"x-cache": "HIT", "x-feed-source": "cache", "x-feed-version": "1"}, {"items": [{"id": 1}]}),
            (200, {"x-cache": "MISS", "x-feed-source": "db", "x-feed-version": "2"}, {"items": [{"id": 2}], "session_id": "s1"}),
            (200, {"x-cache": "MISS", "x-feed-source": "db", "x-feed-version": "3"}, {"items": [{"id": 3}]}),
            (200, {"x-cache": "HIT", "x-feed-source": "cache", "x-feed-version": "3"}, {"items": [{"id": 3}]}),
            (200, {"x-cache": "MISS", "x-feed-source": "db", "x-feed-version": "4"}, {"items": [{"id": 4}], "session_id": "s2"}),
            (200, {"x-cache": "MISS", "x-feed-source": "db", "x-feed-version": "5"}, {"items": [{"id": 5}]}),
            (200, {"x-cache": "HIT", "x-feed-source": "cache", "x-feed-version": "5"}, {"items": [{"id": 5}]}),
            (200, {"content-type": "application/json"}, {"ok": True}),
            (200, {"content-type": "application/json"}, {"ok": True}),
            (200, {"content-type": "application/json"}, {"ok": True}),
            (200, {"content-type": "application/json"}, {"ok": True}),
            (200, {"x-feed-version": "2"}, {"items": [{"id": 2}]}),
        ]
    )

    monkeypatch.setattr(operational_check, "_request_json", lambda *_args, **_kwargs: next(responses))

    report = operational_check.run("https://example.com", timeout=5.0, admin_key=None)

    assert report["anomalies"] == [
        "/health reported degraded: Ingestion is expected externally but no scheduler leader lock is visible."
    ]


def test_unexpected_unhealthy_surfaces_ignores_allowed_surfaces():
    unexpected = operational_check._unexpected_unhealthy_surfaces(
        {
            "surfaces": {
                "articles": {"is_healthy": True},
                "reels": {"is_healthy": False},
                "videos": {"is_healthy": False},
            }
        },
        allowed_surfaces={"reels"},
    )

    assert unexpected == ["videos"]


def test_main_posts_alert_when_failures_present(monkeypatch, capsys):
    posted = {}

    monkeypatch.setattr(
        operational_check,
        "run",
        lambda *_args, **_kwargs: {
            "base_url": "https://example.com",
            "anomalies": ["inventory unhealthy (videos)"],
            "health": {"payload": {"status": "degraded"}},
            "admin": {"skipped": True},
        },
    )
    monkeypatch.setattr(
        operational_check,
        "_post_webhook",
        lambda url, payload, timeout: posted.update(
            {"url": url, "payload": payload, "timeout": timeout}
        ),
    )

    exit_code = operational_check.main(
        [
            "--base-url",
            "https://example.com",
            "--fail-on-anomaly",
            "--alert-webhook-url",
            "https://alerts.example.com",
        ]
    )

    assert exit_code == 1
    assert posted["url"] == "https://alerts.example.com"
    assert "inventory unhealthy (videos)" in posted["payload"]["text"]
    assert "Health status: degraded" in posted["payload"]["text"]
    assert "FAIL: inventory unhealthy (videos)" in capsys.readouterr().err


def test_main_warns_when_alert_post_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        operational_check,
        "run",
        lambda *_args, **_kwargs: {
            "base_url": "https://example.com",
            "anomalies": ["articles_recent: empty item list"],
            "health": {"payload": {"status": "degraded"}},
            "admin": {"skipped": True},
        },
    )

    def raise_url_error(_url, _payload, timeout):  # noqa: ARG001
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(operational_check, "_post_webhook", raise_url_error)

    exit_code = operational_check.main(
        [
            "--base-url",
            "https://example.com",
            "--fail-on-anomaly",
            "--alert-webhook-url",
            "https://alerts.example.com",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "WARN: failed to post operational alert" in captured.err


def test_record_detail_and_starter_checks_probes_top_article_and_video(monkeypatch):
    calls = []

    def fake_request_json(base_url, path, **kwargs):
        calls.append((base_url, path, kwargs.get("headers")))
        return 200, {"content-type": "application/json"}, {"ok": True}

    monkeypatch.setattr(operational_check, "_request_json", fake_request_json)

    report = {
        "feeds": {
            "session_articles": {"top_items": [{"id": 101}]},
            "session_videos": {"top_items": [{"id": 202}]},
            "reels": {"top_items": [{"id": 303}]},
        }
    }
    anomalies: list[str] = []

    operational_check._record_detail_and_starter_checks(
        report,
        base_url="https://api.example.com",
        auth_headers={"Authorization": "Bearer token"},
        timeout=5.0,
        anomalies=anomalies,
    )

    assert anomalies == []
    assert report["details"] == {
        "article_detail": {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "payload": {"ok": True},
        },
        "article_starters": {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "payload": {"ok": True},
        },
        "video_detail": {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "payload": {"ok": True},
        },
        "video_starters": {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "payload": {"ok": True},
        },
    }
    assert calls == [
        (
            "https://api.example.com",
            "/api/v1/articles/101",
            {"Authorization": "Bearer token"},
        ),
        ("https://api.example.com", "/api/v1/starters/101", {}),
        (
            "https://api.example.com",
            "/api/v1/videos/202",
            {"Authorization": "Bearer token"},
        ),
        ("https://api.example.com", "/api/v1/starters/202", {}),
    ]
