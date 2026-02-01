"""In-process runtime state for ingestion observability.

This provides a small, thread-safe place to store recent scheduler stats so that
GET /metrics can expose worker pool visibility.

Do not persist secrets.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional


_LOCK = threading.Lock()
_LAST: Dict[str, Any] = {
    "updated_at": None,
    "scheduler": None,
}


def set_scheduler_snapshot(snapshot: Dict[str, Any]) -> None:
    with _LOCK:
        _LAST["updated_at"] = datetime.now(timezone.utc).isoformat()
        _LAST["scheduler"] = snapshot


def get_scheduler_snapshot() -> Dict[str, Optional[Any]]:
    with _LOCK:
        return {
            "updated_at": _LAST.get("updated_at"),
            "scheduler": _LAST.get("scheduler"),
        }
