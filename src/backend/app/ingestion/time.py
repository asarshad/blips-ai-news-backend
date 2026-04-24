from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo


def _ingestion_timezone() -> ZoneInfo:
    tz_name = os.getenv("INGESTION_TIMEZONE", "America/Toronto")
    return ZoneInfo(tz_name)


def get_ingestion_day(*, now: Optional[datetime] = None) -> date:
    """Return the ingestion "today" date in the configured timezone.

    This is used for determining the logical ingestion day (e.g. Toronto/EST).
    Persistence may still use the historical column name `day_utc`.
    """

    tz = _ingestion_timezone()
    current = now or datetime.utcnow()
    # Treat naive `utcnow()` as UTC.
    current = current.replace(tzinfo=ZoneInfo("UTC"))
    return current.astimezone(tz).date()


def get_ingestion_day_bounds(*, day: date) -> tuple[datetime, datetime]:
    """Return naive UTC bounds for the provided ingestion day."""

    tz = _ingestion_timezone()
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    end_utc = end_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return start_utc, end_utc
