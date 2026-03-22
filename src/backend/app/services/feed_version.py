"""Helpers for computing stable feed versions."""

from datetime import datetime
from typing import Any, Dict, List


def compute_feed_version(items: List[Dict[str, Any]], generated_at: datetime) -> str:
    """
    Compute a feed version hash based on content.

    This can be used by clients to determine if cache needs refresh.
    Version changes when:
    - Items list changes
    - Generation time changes significantly
    """
    if not items:
        return f"empty:{generated_at.timestamp():.0f}"

    newest_pub = None
    for item in items:
        pub = item.get("published_at")
        if pub and (newest_pub is None or pub > newest_pub):
            newest_pub = pub

    version_parts = [
        f"n:{len(items)}",
        f"p:{newest_pub or 'none'}",
        f"t:{int(generated_at.timestamp() // 60)}",
    ]

    return "|".join(version_parts)
