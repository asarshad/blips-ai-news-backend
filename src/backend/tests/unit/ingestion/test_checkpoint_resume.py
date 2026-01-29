import threading
from datetime import date

from app.ingestion import checkpointing


class _Row:
    def __init__(self, row_id: int):
        self.id = row_id


class _FakeRepo:
    def __init__(self):
        self._calls = 0

    def list_incomplete(self, *, day_utc: date):
        # First call returns one running row; second call returns empty
        self._calls += 1
        if self._calls == 1:
            return [_Row(1)]
        return []


def test_resume_loop_processes_incomplete_then_completes(monkeypatch):
    repo = _FakeRepo()
    processed = []

    def process_row(row_id: int):
        processed.append(row_id)
        return {"row_id": row_id, "status": "ok", "inserted": 2}

    # Avoid sleeping in tests
    monkeypatch.setattr(checkpointing.time, "sleep", lambda _: None)

    result = checkpointing._run_checkpoint_loop(
        day=date(2026, 1, 29),
        repo=repo,
        process_row=process_row,
        ingest_until_targets=True,
        poll_seconds=0.0,
        max_seconds=1,
        max_workers=1,
        stop_event=threading.Event(),
    )

    assert processed == [1]
    assert result["status"] == "complete"
    assert result["total_inserted"] == 2
