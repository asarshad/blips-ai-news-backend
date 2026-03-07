"""Controlled rollout validation checks for production-like environments."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import requests


@dataclass(frozen=True)
class Check:
    name: str
    path: str
    expected_statuses: Tuple[int, ...] = (200,)
    admin_required: bool = False


def build_checks() -> List[Check]:
    """Return the canonical set of rollout validation checks."""
    return [
        Check(name="health", path="/health"),
        Check(name="inventory_health", path="/api/v1/inventory/health"),
        Check(
            name="session_playlist",
            path="/api/v1/session/playlist?type=ARTICLE&size=20&refresh=true",
        ),
        Check(
            name="source_metrics",
            path="/metrics/sources",
            admin_required=True,
        ),
        Check(
            name="inventory_metrics",
            path="/metrics/inventory/health",
            admin_required=True,
        ),
    ]


def evaluate_inventory_health(payload: Dict) -> List[str]:
    """Validate feed inventory health payload invariants."""
    issues: List[str] = []
    if payload.get("is_healthy") is False:
        issues.append("inventory reports unhealthy state")
    if payload.get("needs_topup") is True:
        issues.append("inventory requests top-up")
    return issues


def evaluate_inventory_metrics(payload: Dict) -> List[str]:
    """Validate source-diversity guardrails for rollout."""
    issues: List[str] = []
    dominant = float(payload.get("dominant_source_pct", 0.0) or 0.0)
    infra = float(payload.get("infra_share_pct", 0.0) or 0.0)

    if dominant > 30.0:
        issues.append(f"dominant source too high: {dominant:.1f}%")
    if infra < 10.0:
        issues.append(f"infra coverage too low: {infra:.1f}%")
    return issues


def run_checks(base_url: str, admin_key: str | None, timeout: float) -> int:
    """Run all rollout checks and return process exit code."""
    failures: List[str] = []
    checks = build_checks()

    for check in checks:
        if check.admin_required and not admin_key:
            print(f"SKIP {check.name}: admin key not provided")
            continue

        headers = {"X-Admin-Key": admin_key} if check.admin_required else {}
        if check.name == "session_playlist":
            headers["X-Device-ID"] = "rollout-validation-device"

        url = f"{base_url.rstrip('/')}{check.path}"
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except Exception as exc:  # pragma: no cover
            failures.append(f"{check.name}: request failed ({exc})")
            continue

        if resp.status_code not in check.expected_statuses:
            failures.append(
                f"{check.name}: unexpected status {resp.status_code} expected {check.expected_statuses}"
            )
            continue

        if check.name == "inventory_health":
            for issue in evaluate_inventory_health(resp.json()):
                failures.append(f"{check.name}: {issue}")
        elif check.name == "inventory_metrics":
            for issue in evaluate_inventory_metrics(resp.json()):
                failures.append(f"{check.name}: {issue}")

        print(f"PASS {check.name}")

    if failures:
        print("\nValidation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nValidation passed.")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run controlled-rollout validation checks.")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL for API checks.",
    )
    parser.add_argument(
        "--admin-key",
        default=None,
        help="Optional admin API key for protected metrics endpoints.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="HTTP timeout in seconds.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return run_checks(
        base_url=args.base_url,
        admin_key=args.admin_key,
        timeout=float(args.timeout),
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
