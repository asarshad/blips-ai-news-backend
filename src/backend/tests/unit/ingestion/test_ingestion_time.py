from datetime import datetime

from app.ingestion.time import get_ingestion_day


def test_get_ingestion_day_defaults_to_toronto(monkeypatch):
    # Ensure default is America/Toronto (UTC-5 in January)
    monkeypatch.delenv("INGESTION_TIMEZONE", raising=False)

    # 02:00 UTC on Jan 30 is 21:00 previous day in Toronto.
    day = get_ingestion_day(now=datetime(2026, 1, 30, 2, 0, 0))
    assert day.isoformat() == "2026-01-29"
