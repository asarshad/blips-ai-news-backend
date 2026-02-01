from __future__ import annotations

import os
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo


def get_ingestion_day(*, now: Optional[datetime] = None) -> date:
    """Return the ingestion "today" date in the configured timezone.

    This is used for determining the logical ingestion day (e.g. Toronto/EST).
    Persistence may still use the historical column name `day_utc`.
    """

    tz_name = os.getenv("INGESTION_TIMEZONE", "America/Toronto")
    tz = ZoneInfo(tz_name)

    current = now or datetime.utcnow()
    # Treat naive `utcnow()` as UTC.
    current = current.replace(tzinfo=ZoneInfo("UTC"))
    return current.astimezone(tz).date()
