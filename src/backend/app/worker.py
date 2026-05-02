"""Backward-compatible worker entrypoint.

Render now starts ``app.workers.launcher`` directly, but ``python -m app.worker``
continues to work for local scripts and older runbooks.
"""

from __future__ import annotations

from app.workers.launcher import run_launcher


def run_worker() -> int:
    return run_launcher()


if __name__ == "__main__":
    raise SystemExit(run_worker())
