"""
Feed response headers for debugging staleness issues.

Adds diagnostic headers to all feed responses:
- X-Feed-Generated-At: Server timestamp when response was generated
- X-Feed-Source: "redis" | "db" | "mixed"
- X-Feed-Key: Redis cache key used (if applicable)
- X-Cache: "HIT" | "MISS" | "BYPASS"
- X-Newest-Published-At: Newest published_at in returned items
- X-Newest-Created-At: Newest created_at in returned items
- X-Query-Window: Tier window config summary
- X-Feed-Version: Monotonic version for cache invalidation
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from starlette.responses import Response


@dataclass
class FeedMetadata:
    """Metadata about a feed response for headers."""

    generated_at: datetime
    source: str  # "redis" | "db"
    cache_key: Optional[str] = None
    cache_hit: bool = False
    items: List[Dict[str, Any]] = None
    surface: str = ""
    tier_config: Optional[Dict[str, int]] = None
    feed_version: Optional[str] = None

    def get_newest_dates(self) -> tuple:
        """Extract newest published_at and created_at from items."""
        if not self.items:
            return None, None

        newest_published = None
        newest_created = None

        for item in self.items:
            pub = item.get("published_at")
            created = item.get("created_at")

            if pub and (newest_published is None or pub > newest_published):
                newest_published = pub
            if created and (newest_created is None or created > newest_created):
                newest_created = created

        return newest_published, newest_created

    def add_headers(self, response: Response) -> None:
        """Add diagnostic headers to a response."""
        response.headers["X-Feed-Generated-At"] = self.generated_at.isoformat()
        response.headers["X-Feed-Source"] = self.source

        if self.cache_key:
            response.headers["X-Feed-Key"] = self.cache_key

        response.headers["X-Cache"] = "HIT" if self.cache_hit else "MISS"

        newest_pub, newest_created = self.get_newest_dates()
        if newest_pub:
            response.headers["X-Newest-Published-At"] = newest_pub
        if newest_created:
            response.headers["X-Newest-Created-At"] = newest_created

        if self.tier_config:
            window_desc = f"A:{self.tier_config.get('fresh_hours', '?')}h,B:{self.tier_config.get('backfill_hours', '?')}h,C:{self.tier_config.get('evergreen_days', '?')}d"
            response.headers["X-Query-Window"] = window_desc

        if self.feed_version:
            response.headers["X-Feed-Version"] = self.feed_version


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

    # Use a combination of: newest published_at + item count + generation time
    # This is simple but effective for cache invalidation
    newest_pub = None
    for item in items:
        pub = item.get("published_at")
        if pub and (newest_pub is None or pub > newest_pub):
            newest_pub = pub

    version_parts = [
        f"n:{len(items)}",
        f"p:{newest_pub or 'none'}",
        f"t:{int(generated_at.timestamp() // 60)}",  # Minute precision
    ]

    return "|".join(version_parts)
