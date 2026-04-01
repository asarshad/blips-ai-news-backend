#!/usr/bin/env python3
"""Lightweight operational probe for Blips production-like environments.

Checks:
- /health
- public inventory health
- anonymous session bootstrap
- articles/videos/reels feed endpoints used by clients
- optional admin endpoints when ADMIN_API_KEY is provided

Outputs a JSON summary with counts, freshness timestamps, cache headers, and
detected anomalies so operators can compare runs over time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


DEFAULT_BASE_URL = "https://api.blips.tech"
DEFAULT_APP_VERSION = "ops-check"
DEFAULT_PLATFORM = "ios"
DEFAULT_TIMEOUT = 20.0


@dataclass(frozen=True)
class EndpointCheck:
    name: str
    path: str
    uses_session: bool = True
    repeat_for_cache: bool = False


FEED_CHECKS = [
    EndpointCheck(
        name="articles_recent",
        path="/api/v1/articles/recent?limit=5",
        repeat_for_cache=True,
    ),
    EndpointCheck(
        name="session_articles",
        path="/api/v1/session/playlist?type=ARTICLE&size=20",
    ),
    EndpointCheck(
        name="videos_recent",
        path="/api/v1/videos/recent?limit=5",
        repeat_for_cache=True,
    ),
    EndpointCheck(
        name="session_videos",
        path="/api/v1/session/playlist?type=VIDEO&size=20",
    ),
    EndpointCheck(
        name="reels",
        path="/api/v1/videos/reels?limit=10",
        repeat_for_cache=True,
    ),
]

INTERESTING_HEADERS = {
    "content-type",
    "x-cache",
    "x-feed-generated-at",
    "x-feed-key",
    "x-feed-source",
    "x-feed-version",
    "x-newest-created-at",
    "x-newest-published-at",
    "x-process-time",
    "x-query-window",
}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, dict[str, str], Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    encoded = None
    request_headers = dict(headers or {})
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            return (
                response.status,
                dict(response.headers.items()),
                json.loads(payload) if payload else None,
            )
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        return (
            exc.code,
            dict(exc.headers.items()),
            json.loads(payload) if payload else None,
        )


def _interesting_headers(headers: dict[str, str]) -> dict[str, str]:
    filtered: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in INTERESTING_HEADERS:
            filtered[lower] = value
    return filtered


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("items", "articles", "videos"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _newest(items: list[dict[str, Any]], key: str) -> str | None:
    values = [str(item.get(key)) for item in items if item.get(key)]
    return max(values) if values else None


def _check_feed(
    base_url: str,
    check: EndpointCheck,
    *,
    auth_headers: dict[str, str],
    timeout: float,
    anomalies: list[str],
) -> dict[str, Any]:
    status, headers, payload = _request_json(
        base_url,
        check.path,
        headers=auth_headers,
        timeout=timeout,
    )
    items = _extract_items(payload)
    result: dict[str, Any] = {
        "status_code": status,
        "headers": _interesting_headers(headers),
        "item_count": len(items),
        "session_id": payload.get("session_id") if isinstance(payload, dict) else None,
        "inventory_state": payload.get("inventory_state") if isinstance(payload, dict) else None,
        "has_more": payload.get("has_more") if isinstance(payload, dict) else None,
        "served_at": payload.get("served_at") if isinstance(payload, dict) else None,
        "payload_newest_published_at": payload.get("newest_published_at")
        if isinstance(payload, dict)
        else None,
        "payload_newest_created_at": payload.get("newest_created_at")
        if isinstance(payload, dict)
        else None,
        "computed_newest_published_at": _newest(items, "published_at"),
        "computed_newest_created_at": _newest(items, "created_at"),
        "top_items": [
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "title": item.get("title"),
                "source": item.get("source"),
                "published_at": item.get("published_at"),
                "created_at": item.get("created_at"),
                "freshness_tier": item.get("freshness_tier"),
            }
            for item in items[:3]
        ],
    }

    if status != 200:
        anomalies.append(f"{check.name}: unexpected status {status}")
        return result

    if not items:
        anomalies.append(f"{check.name}: empty item list")

    required_headers = ("x-cache", "x-feed-source", "x-feed-version")
    missing = [name for name in required_headers if name not in result["headers"]]
    if missing:
        anomalies.append(f"{check.name}: missing headers {', '.join(missing)}")

    payload_newest_published = result["payload_newest_published_at"]
    header_newest_published = result["headers"].get("x-newest-published-at")
    if payload_newest_published and header_newest_published:
        if payload_newest_published != header_newest_published:
            anomalies.append(
                f"{check.name}: payload/header newest_published_at mismatch "
                f"({payload_newest_published} vs {header_newest_published})"
            )

    payload_newest_created = result["payload_newest_created_at"]
    header_newest_created = result["headers"].get("x-newest-created-at")
    if payload_newest_created and header_newest_created:
        if payload_newest_created != header_newest_created:
            anomalies.append(
                f"{check.name}: payload/header newest_created_at mismatch "
                f"({payload_newest_created} vs {header_newest_created})"
            )

    if check.repeat_for_cache:
        status_2, headers_2, payload_2 = _request_json(
            base_url,
            check.path,
            headers=auth_headers,
            timeout=timeout,
        )
        repeat = {
            "status_code": status_2,
            "headers": _interesting_headers(headers_2),
            "top_item_ids": [item.get("id") for item in _extract_items(payload_2)[:3]],
        }
        result["repeat_check"] = repeat
        if status_2 == 200 and repeat["headers"].get("x-cache") != "HIT":
            anomalies.append(f"{check.name}: second request did not hit cache")
        if (
            status == 200
            and status_2 == 200
            and result["headers"].get("x-feed-version")
            == repeat["headers"].get("x-feed-version")
            and result["headers"].get("x-feed-generated-at")
            != repeat["headers"].get("x-feed-generated-at")
        ):
            anomalies.append(
                f"{check.name}: x-feed-generated-at changes across cache HITs with stable feed_version"
            )

    return result


def _normalize_surface_set(values: list[str] | None) -> set[str]:
    return {value.strip().lower() for value in (values or []) if value and value.strip()}


def _unexpected_unhealthy_surfaces(
    inventory_payload: Any,
    *,
    allowed_surfaces: set[str],
) -> list[str]:
    if not isinstance(inventory_payload, dict):
        return []

    surfaces = inventory_payload.get("surfaces", {})
    unhealthy = [
        str(surface).lower()
        for surface, details in surfaces.items()
        if isinstance(details, dict) and details.get("is_healthy") is False
    ]
    return [surface for surface in unhealthy if surface not in allowed_surfaces]


def _check_content_endpoint(
    base_url: str,
    *,
    name: str,
    path: str,
    headers: dict[str, str],
    timeout: float,
    anomalies: list[str],
) -> dict[str, Any]:
    status, response_headers, payload = _request_json(
        base_url,
        path,
        headers=headers,
        timeout=timeout,
    )
    result = {
        "status_code": status,
        "headers": _interesting_headers(response_headers),
        "payload": payload,
    }
    if status != 200 or not isinstance(payload, dict):
        anomalies.append(f"{name}: unexpected status {status}")
    return result


def _record_detail_and_starter_checks(
    report: dict[str, Any],
    *,
    base_url: str,
    auth_headers: dict[str, str],
    timeout: float,
    anomalies: list[str],
) -> None:
    feeds = report.get("feeds", {})
    if not isinstance(feeds, dict):
        return

    derived: dict[str, Any] = {}

    article_items = feeds.get("session_articles", {}).get("top_items", [])
    if article_items:
        article_id = article_items[0].get("id")
        if article_id is not None:
            derived["article_detail"] = _check_content_endpoint(
                base_url,
                name="article_detail",
                path=f"/api/v1/articles/{article_id}",
                headers=auth_headers,
                timeout=timeout,
                anomalies=anomalies,
            )
            derived["article_starters"] = _check_content_endpoint(
                base_url,
                name="article_starters",
                path=f"/api/v1/starters/{article_id}",
                headers={},
                timeout=timeout,
                anomalies=anomalies,
            )

    video_candidates = []
    for feed_name in ("session_videos", "reels"):
        items = feeds.get(feed_name, {}).get("top_items", [])
        if items:
            video_candidates = items
            break
    if video_candidates:
        video_id = video_candidates[0].get("id")
        if video_id is not None:
            derived["video_detail"] = _check_content_endpoint(
                base_url,
                name="video_detail",
                path=f"/api/v1/videos/{video_id}",
                headers=auth_headers,
                timeout=timeout,
                anomalies=anomalies,
            )
            derived["video_starters"] = _check_content_endpoint(
                base_url,
                name="video_starters",
                path=f"/api/v1/starters/{video_id}",
                headers={},
                timeout=timeout,
                anomalies=anomalies,
            )

    if derived:
        report["details"] = derived


def run(
    base_url: str,
    *,
    timeout: float,
    admin_key: str | None,
    allowed_unhealthy_surfaces: set[str] | None = None,
) -> dict[str, Any]:
    anomalies: list[str] = []
    report: dict[str, Any] = {
        "checked_at": _utcnow(),
        "base_url": base_url.rstrip("/"),
        "anomalies": anomalies,
    }
    allowed_surfaces = _normalize_surface_set(list(allowed_unhealthy_surfaces or []))

    health_status, health_headers, health_payload = _request_json(
        base_url,
        "/health",
        timeout=timeout,
    )
    report["health"] = {
        "status_code": health_status,
        "headers": _interesting_headers(health_headers),
        "payload": health_payload,
    }
    if health_status != 200 or not isinstance(health_payload, dict):
        anomalies.append("/health failed")
        return report

    inventory_status, inventory_headers, inventory_payload = _request_json(
        base_url,
        "/api/v1/inventory/health",
        timeout=timeout,
    )
    report["inventory_health"] = {
        "status_code": inventory_status,
        "headers": _interesting_headers(inventory_headers),
        "payload": inventory_payload,
        "allowed_unhealthy_surfaces": sorted(allowed_surfaces),
    }
    if inventory_status != 200:
        anomalies.append("/api/v1/inventory/health failed")
    elif isinstance(inventory_payload, dict) and inventory_payload.get("is_healthy") is False:
        unexpected_unhealthy = _unexpected_unhealthy_surfaces(
            inventory_payload,
            allowed_surfaces=allowed_surfaces,
        )
        if unexpected_unhealthy:
            anomalies.append(
                "inventory unhealthy"
                + (f" ({', '.join(unexpected_unhealthy)})" if unexpected_unhealthy else "")
            )

    session_status, _session_headers, session_payload = _request_json(
        base_url,
        "/api/v1/auth/session",
        method="POST",
        body={
            "platform": DEFAULT_PLATFORM,
            "app_version": DEFAULT_APP_VERSION,
        },
        timeout=timeout,
    )
    report["session_bootstrap"] = {
        "status_code": session_status,
        "device_id": session_payload.get("device_id")
        if isinstance(session_payload, dict)
        else None,
        "expires_in": session_payload.get("expires_in")
        if isinstance(session_payload, dict)
        else None,
    }
    if session_status != 200 or not isinstance(session_payload, dict):
        anomalies.append("anonymous session bootstrap failed")
        return report

    access_token = str(session_payload["access_token"])
    auth_headers = {"Authorization": f"Bearer {access_token}"}
    feed_results: dict[str, Any] = {}
    for check in FEED_CHECKS:
        feed_results[check.name] = _check_feed(
            base_url,
            check,
            auth_headers=auth_headers,
            timeout=timeout,
            anomalies=anomalies,
        )
    report["feeds"] = feed_results
    _record_detail_and_starter_checks(
        report,
        base_url=base_url,
        auth_headers=auth_headers,
        timeout=timeout,
        anomalies=anomalies,
    )

    session_articles = feed_results.get("session_articles", {})
    same_session_id = session_articles.get("session_id")
    if session_articles.get("status_code") == 200 and same_session_id:
        reused_status, reused_headers, reused_payload = _request_json(
            base_url,
            f"/api/v1/session/playlist?type=ARTICLE&size=20&session_id={same_session_id}",
            headers=auth_headers,
            timeout=timeout,
        )
        report["session_reuse"] = {
            "status_code": reused_status,
            "headers": _interesting_headers(reused_headers),
            "session_id": same_session_id,
            "same_feed_version": isinstance(reused_payload, dict)
            and session_articles.get("headers", {}).get("x-feed-version")
            == _interesting_headers(reused_headers).get("x-feed-version"),
            "same_first_ids": isinstance(reused_payload, dict)
            and [item.get("id") for item in session_articles.get("top_items", [])[:3]]
            == [item.get("id") for item in _extract_items(reused_payload)[:3]],
        }

    if admin_key:
        admin_headers = {"X-Admin-Key": admin_key}
        admin_results = {}
        for name, path in (
            ("metrics", "/metrics"),
            ("ops_status", "/ops/status"),
            ("source_metrics", "/api/v1/metrics/sources"),
            ("video_source_metrics", "/api/v1/metrics/video-sources"),
        ):
            status, headers, payload = _request_json(
                base_url,
                path,
                headers=admin_headers,
                timeout=timeout,
            )
            admin_results[name] = {
                "status_code": status,
                "headers": _interesting_headers(headers),
                "payload": payload,
            }
            if status != 200:
                anomalies.append(f"{path} returned {status}")
        report["admin"] = admin_results
    else:
        report["admin"] = {
            "skipped": True,
            "reason": "ADMIN_API_KEY not provided",
        }

    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL for the deployed API, for example https://api.blips.tech",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--admin-key",
        default=os.getenv("ADMIN_API_KEY"),
        help="Optional admin key for protected operational endpoints",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON report",
    )
    parser.add_argument(
        "--fail-on-anomaly",
        action="store_true",
        help="Exit non-zero when anomalies are detected",
    )
    parser.add_argument(
        "--require-admin-success",
        action="store_true",
        help="Exit non-zero when admin checks are skipped or fail",
    )
    parser.add_argument(
        "--allow-unhealthy-surface",
        action="append",
        default=[],
        help="Surface name to tolerate as known degraded inventory without failing",
    )
    return parser.parse_args(argv)


def evaluate_report_failures(
    report: dict[str, Any],
    *,
    fail_on_anomaly: bool,
    require_admin_success: bool,
) -> list[str]:
    failures: list[str] = []
    if fail_on_anomaly:
        failures.extend(str(item) for item in report.get("anomalies", []) if item)

    if require_admin_success:
        admin = report.get("admin")
        if not isinstance(admin, dict) or admin.get("skipped"):
            failures.append("admin checks skipped")
        else:
            for name, details in admin.items():
                if not isinstance(details, dict):
                    continue
                if int(details.get("status_code", 0) or 0) != 200:
                    failures.append(f"admin check failed: {name}")

    return failures


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = run(
        args.base_url,
        timeout=float(args.timeout),
        admin_key=args.admin_key,
        allowed_unhealthy_surfaces=_normalize_surface_set(args.allow_unhealthy_surface),
    )
    if args.pretty:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, sort_keys=True))
    failures = evaluate_report_failures(
        report,
        fail_on_anomaly=bool(args.fail_on_anomaly),
        require_admin_success=bool(args.require_admin_success),
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
