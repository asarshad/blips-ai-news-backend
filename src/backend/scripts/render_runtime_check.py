#!/usr/bin/env python3
"""Check recent Render worker events for runtime failures.

This script is intended for scheduled CI or manual operational runs. It polls
recent service events from the Render API, highlights OOM/restart failures, and
can optionally post a webhook alert.
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
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_TIMEOUT = 20.0
DEFAULT_RECENT_HOURS = 24
DEFAULT_WORKER_SERVICE_ID = "srv-d6hvve8gjchc73d0cq70"


@dataclass(frozen=True)
class RuntimeAnomaly:
    timestamp: str
    event_type: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "summary": self.summary,
        }


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _request_json(url: str, *, token: str, timeout: float) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
        return json.loads(payload) if payload else None


def _post_webhook(webhook_url: str, payload: dict[str, Any], *, timeout: float) -> None:
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout):
        return


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def summarize_events(
    events_payload: list[dict[str, Any]],
    *,
    recent_hours: int,
) -> dict[str, Any]:
    cutoff = _utcnow() - timedelta(hours=recent_hours)
    recent_events: list[dict[str, Any]] = []
    anomalies: list[RuntimeAnomaly] = []

    for wrapper in events_payload:
        event = wrapper.get("event", wrapper)
        timestamp = _parse_timestamp(event.get("timestamp"))
        if timestamp is None or timestamp < cutoff:
            continue

        event_type = str(event.get("type") or "unknown")
        details = event.get("details") or {}
        recent_events.append(
            {
                "timestamp": timestamp.isoformat(),
                "type": event_type,
                "details": details,
            }
        )

        if event_type == "server_failed":
            reason = details.get("reason") or {}
            oom = reason.get("oomKilled") if isinstance(reason, dict) else None
            if isinstance(oom, dict):
                summary = f"oomKilled at limit {oom.get('memoryLimit', 'unknown')}"
            else:
                summary = "server_failed"
            anomalies.append(
                RuntimeAnomaly(
                    timestamp=timestamp.isoformat(),
                    event_type=event_type,
                    summary=summary,
                )
            )
        elif event_type == "deploy_ended" and details.get("deployStatus") not in {
            "succeeded",
            2,
        }:
            anomalies.append(
                RuntimeAnomaly(
                    timestamp=timestamp.isoformat(),
                    event_type=event_type,
                    summary=f"deploy ended with status {details.get('deployStatus')}",
                )
            )

    return {
        "window_hours": recent_hours,
        "recent_event_count": len(recent_events),
        "recent_events": recent_events[:20],
        "anomalies": [item.to_dict() for item in anomalies],
    }


def build_alert_payload(*, service_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    lines = [
        f"Render runtime monitor detected {len(summary['anomalies'])} anomaly/anomalies "
        f"for service {service_id} in the last {summary['window_hours']}h."
    ]
    for anomaly in summary["anomalies"][:5]:
        lines.append(f"- {anomaly['timestamp']}: {anomaly['summary']}")
    return {"text": "\n".join(lines)}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-id", default=os.getenv("RENDER_WORKER_SERVICE_ID", DEFAULT_WORKER_SERVICE_ID))
    parser.add_argument("--recent-hours", type=int, default=DEFAULT_RECENT_HOURS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--render-api-token", default=os.getenv("RENDER_API_TOKEN"))
    parser.add_argument("--alert-webhook-url", default=os.getenv("ALERT_WEBHOOK_URL"))
    parser.add_argument("--fail-on-anomaly", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if not args.render_api_token:
        print(json.dumps({"error": "RENDER_API_TOKEN is required"}))
        return 2

    url = (
        "https://api.render.com/v1/services/"
        f"{urllib.parse.quote(args.service_id, safe='')}/events?limit=50"
    )
    try:
        events_payload = _request_json(url, token=args.render_api_token, timeout=args.timeout)
    except urllib.error.HTTPError as exc:
        print(json.dumps({"error": f"Render API returned HTTP {exc.code}"}))
        return 2

    summary = {
        "checked_at": _utcnow().isoformat(),
        "service_id": args.service_id,
    }
    summary.update(summarize_events(events_payload or [], recent_hours=args.recent_hours))

    if summary["anomalies"] and args.alert_webhook_url:
        try:
            _post_webhook(
                args.alert_webhook_url,
                build_alert_payload(service_id=args.service_id, summary=summary),
                timeout=args.timeout,
            )
            summary["alert_sent"] = True
        except urllib.error.URLError as exc:
            summary["alert_sent"] = False
            summary["alert_error"] = str(exc)

    print(json.dumps(summary, indent=2 if args.pretty else None, sort_keys=True))
    if args.fail_on_anomaly and summary["anomalies"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
