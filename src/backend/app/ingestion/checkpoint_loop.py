"""Core resume loop for checkpointed ingestion (unit-testable)."""

from __future__ import annotations

import threading
import time
from datetime import date
from typing import Callable, Dict

from app.repositories.ingestion_progress_repo import IngestionProgressRepository


def run_checkpoint_loop(
    *,
    day: date,
    repo: IngestionProgressRepository,
    process_row: Callable[[int], Dict[str, object]],
    ingest_until_targets: bool,
    poll_seconds: float,
    max_seconds: int,
    max_workers: int,
    stop_event: threading.Event,
) -> Dict[str, object]:
    start = time.monotonic()
    total_inserted = 0

    while True:
        if stop_event.is_set():
            return {
                "status": "stopping",
                "day_utc": day.isoformat(),
                "total_inserted": total_inserted,
            }

        incomplete = repo.list_incomplete(day_utc=day)
        if not incomplete:
            return {
                "status": "complete",
                "day_utc": day.isoformat(),
                "total_inserted": total_inserted,
            }

        if max_workers <= 1:
            for row in incomplete:
                if stop_event.is_set():
                    return {
                        "status": "stopping",
                        "day_utc": day.isoformat(),
                        "total_inserted": total_inserted,
                    }

                if time.monotonic() - start >= max_seconds:
                    return {
                        "status": "budget_exhausted",
                        "day_utc": day.isoformat(),
                        "total_inserted": total_inserted,
                        "remaining_rows": len(incomplete),
                    }

                result = process_row(int(row.id))
                total_inserted += int(result.get("inserted") or 0)
        else:
            from concurrent.futures import ThreadPoolExecutor

            ids = [int(r.id) for r in incomplete]
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for result in ex.map(process_row, ids):
                    total_inserted += int((result or {}).get("inserted") or 0)

        if not ingest_until_targets:
            return {
                "status": "partial",
                "day_utc": day.isoformat(),
                "total_inserted": total_inserted,
                "remaining_rows": len(repo.list_incomplete(day_utc=day)),
            }

        if time.monotonic() - start >= max_seconds:
            return {
                "status": "budget_exhausted",
                "day_utc": day.isoformat(),
                "total_inserted": total_inserted,
                "remaining_rows": len(repo.list_incomplete(day_utc=day)),
            }

        time.sleep(poll_seconds)
