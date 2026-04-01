"""Bounded parallel ingestion scheduler.

Implements a fair task-queue runner over ingestion_progress rows.

Key properties:
- Bounded concurrency (ThreadPoolExecutor)
- Fairness across content types (ARTICLE / VIDEO / REEL)
- Resume-safe: progress is checkpointed in DB
- Safe under restarts: tasks are idempotent + protected by leases
"""

from __future__ import annotations

import os
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque, Dict, Iterable, List, Optional

from app.core.logging import get_logger
from app.ingestion.leases import lease_key

logger = get_logger(__name__)


SOURCE_TYPE_TO_CONTENT_TYPE: Dict[str, str] = {
    "rss": "ARTICLE",
    "youtube_video": "VIDEO",
    "youtube_reel": "REEL",
}


@dataclass(frozen=True)
class SchedulerConfig:
    max_workers: int
    max_workers_article: int
    max_workers_video: int
    max_workers_reel: int
    share_video_slot_with_reels: bool
    batch_size: int
    loop_sleep_seconds: float


@dataclass(frozen=True)
class TaskRef:
    row_id: int
    source_type: str
    feed_name: str


@dataclass
class SchedulerStats:
    max_workers: int
    active_workers: int
    queue_depth: int
    per_type_active: Dict[str, int]
    per_type_caps: Dict[str, int]
    last_dispatch_at: Optional[str]
    last_refresh_at: Optional[str]
    last_cycle_status: str


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def load_scheduler_config() -> SchedulerConfig:
    max_workers = max(1, _int_env("INGESTION_MAX_WORKERS", 1))
    share_video_slot_with_reels = False

    w_a = _int_env("INGESTION_MAX_WORKERS_ARTICLE", 0)
    w_v = _int_env("INGESTION_MAX_WORKERS_VIDEO", 0)
    w_r = _int_env("INGESTION_MAX_WORKERS_REEL", 0)

    # If per-type caps aren’t explicitly set, derive fair defaults.
    if w_a <= 0 and w_v <= 0 and w_r <= 0:
        if max_workers >= 3:
            # Baseline: at least 1 slot each.
            remaining = max_workers - 3
            w_a = 1 + remaining // 3
            w_v = 1 + remaining // 3
            w_r = max_workers - w_a - w_v
        elif max_workers == 2:
            # Prefer ARTICLE + VIDEO, REEL shares VIDEO slot.
            w_a, w_v, w_r = 1, 1, 0
            share_video_slot_with_reels = True
        else:
            w_a, w_v, w_r = 1, 0, 0

    # Hard safety: sum of caps <= max_workers
    total_caps = max(0, w_a) + max(0, w_v) + max(0, w_r)
    if total_caps > max_workers:
        # Reduce REEL first, then VIDEO, then ARTICLE.
        overflow = total_caps - max_workers
        take = min(overflow, max(0, w_r))
        w_r -= take
        overflow -= take
        take = min(overflow, max(0, w_v))
        w_v -= take
        overflow -= take
        take = min(overflow, max(0, w_a))
        w_a -= take

    # Safety: never starve ARTICLE entirely when workers exist.
    # Explicit per-type env values can accidentally set ARTICLE=0 while
    # allocating all workers to VIDEO/REEL, causing RSS ingestion to stop.
    if max_workers > 0 and w_a <= 0:
        overflow = max(0, (max(0, w_a) + max(0, w_v) + max(0, w_r) + 1) - max_workers)
        while overflow > 0:
            if w_v >= w_r and w_v > 0:
                w_v -= 1
            elif w_r > 0:
                w_r -= 1
            else:
                break
            overflow -= 1
        w_a = 1
        logger.warning(
            "Adjusted scheduler caps to preserve ARTICLE capacity: article=%s video=%s reel=%s",
            w_a,
            w_v,
            w_r,
        )

    batch_size = max(1, _int_env("INGESTION_BATCH_SIZE", 10))
    loop_sleep_seconds = _float_env("INGESTION_LOOP_SLEEP_SECONDS", 10.0)

    return SchedulerConfig(
        max_workers=max_workers,
        max_workers_article=max(0, w_a),
        max_workers_video=max(0, w_v),
        max_workers_reel=max(0, w_r),
        share_video_slot_with_reels=share_video_slot_with_reels,
        batch_size=batch_size,
        loop_sleep_seconds=loop_sleep_seconds,
    )


def _content_type_for_source_type(source_type: str) -> str:
    return SOURCE_TYPE_TO_CONTENT_TYPE.get(source_type, source_type)


def build_fair_queue(tasks: Iterable[TaskRef]) -> Deque[TaskRef]:
    """Stable, fair interleaving across known types."""
    by_type: Dict[str, List[TaskRef]] = {"rss": [], "youtube_video": [], "youtube_reel": []}
    for t in tasks:
        by_type.setdefault(t.source_type, []).append(t)

    order = ["rss", "youtube_video", "youtube_reel"]
    idx = {k: 0 for k in by_type.keys()}

    out: List[TaskRef] = []
    while True:
        added = 0
        for k in order:
            lst = by_type.get(k) or []
            i = idx.get(k, 0)
            if i < len(lst):
                out.append(lst[i])
                idx[k] = i + 1
                added += 1
        if added == 0:
            break

    return deque(out)


class IngestionScheduler:
    def __init__(self, *, day_utc, redis_client=None, config: Optional[SchedulerConfig] = None):
        self.day_utc = day_utc
        self.redis_client = redis_client
        self.config = config or load_scheduler_config()

        self._queue: Deque[TaskRef] = deque()
        self._active: Dict[int, TaskRef] = {}
        self._last_dispatch: Optional[datetime] = None
        self._last_refresh: Optional[datetime] = None
        self._last_cycle_status: str = "init"

    def stats(self) -> SchedulerStats:
        per_type_active = Counter(
            _content_type_for_source_type(t.source_type) for t in self._active.values()
        )
        return SchedulerStats(
            max_workers=self.config.max_workers,
            active_workers=len(self._active),
            queue_depth=len(self._queue),
            per_type_active=dict(per_type_active),
            per_type_caps={
                "ARTICLE": self.config.max_workers_article,
                "VIDEO": self.config.max_workers_video,
                "REEL": self.config.max_workers_reel,
            },
            last_dispatch_at=self._last_dispatch.isoformat() if self._last_dispatch else None,
            last_refresh_at=self._last_refresh.isoformat() if self._last_refresh else None,
            last_cycle_status=self._last_cycle_status,
        )

    def refresh(self, *, tasks: List[TaskRef]) -> None:
        active_ids = set(self._active.keys())
        self._queue = build_fair_queue([t for t in tasks if t.row_id not in active_ids])
        self._last_refresh = datetime.now(timezone.utc)

    def _lease_looks_free(self, task: TaskRef) -> bool:
        if self.redis_client is None:
            return True
        try:
            key = lease_key(self.day_utc.isoformat(), task.source_type, task.feed_name)
            return not bool(self.redis_client.exists(key))
        except Exception:
            # If Redis is flaky, we don’t want to stall the scheduler.
            return True

    def _type_has_capacity(self, source_type: str) -> bool:
        ct = _content_type_for_source_type(source_type)
        active = Counter(
            _content_type_for_source_type(t.source_type) for t in self._active.values()
        )
        if ct == "ARTICLE":
            return active.get(ct, 0) < self.config.max_workers_article
        if ct == "VIDEO":
            if self.config.share_video_slot_with_reels:
                return (active.get("VIDEO", 0) + active.get("REEL", 0)) < self.config.max_workers_video
            return active.get(ct, 0) < self.config.max_workers_video
        if ct == "REEL":
            if self.config.share_video_slot_with_reels:
                return (active.get("VIDEO", 0) + active.get("REEL", 0)) < self.config.max_workers_video
            return active.get(ct, 0) < self.config.max_workers_reel
        return True

    def pop_next_dispatchable(self) -> Optional[TaskRef]:
        """Pick next task that respects caps and appears lease-free.

        Fairness is handled by the queue ordering.
        """
        for _ in range(len(self._queue)):
            t = self._queue.popleft()
            if t.row_id in self._active:
                continue
            if not self._type_has_capacity(t.source_type):
                self._queue.append(t)
                continue
            if not self._lease_looks_free(t):
                self._queue.append(t)
                continue
            return t
        return None

    def mark_active(self, task: TaskRef) -> None:
        self._active[task.row_id] = task
        self._last_dispatch = datetime.now(timezone.utc)

    def mark_done(self, row_id: int) -> None:
        self._active.pop(int(row_id), None)

    def run(
        self,
        *,
        fetch_tasks: callable,
        process_task_batch: callable,
        stop_event,
        max_seconds: int,
        sleep_seconds: Optional[float] = None,
        on_stats: Optional[callable] = None,
    ) -> Dict[str, object]:
        """Run scheduler loop until stop/budget/complete.

        fetch_tasks() -> List[TaskRef] must not share DB sessions with workers.
        process_task_batch(row_id, batch_size) runs one batch and returns a dict.
        """
        start = time.monotonic()
        sleep_for = (
            self.config.loop_sleep_seconds if sleep_seconds is None else float(sleep_seconds)
        )

        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        total_inserted = 0
        total_attempted = 0
        futures = {}

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as ex:
            while True:
                if stop_event.is_set():
                    self._last_cycle_status = "stopping"
                    break

                if time.monotonic() - start >= max_seconds:
                    self._last_cycle_status = "budget_exhausted"
                    break

                # Refresh only when the queue is drained.
                #
                # Rebuilding the queue on every cycle when a worker slot is
                # available causes starvation: the scheduler repeatedly picks
                # the first few rows and never rotates through the rest.
                if not self._queue:
                    tasks = fetch_tasks() or []
                    if not tasks and not self._active:
                        self._last_cycle_status = "complete"
                        break
                    self.refresh(tasks=tasks)

                # Dispatch into available worker slots.
                while len(self._active) < self.config.max_workers:
                    task = self.pop_next_dispatchable()
                    if task is None:
                        break

                    self.mark_active(task)
                    logger.info(
                        "ingestion.task_picked: row=%s type=%s feed=%s",
                        task.row_id,
                        task.source_type,
                        task.feed_name,
                    )
                    fut = ex.submit(process_task_batch, task.row_id, self.config.batch_size)
                    futures[fut] = task

                if on_stats:
                    on_stats(self.stats())

                if not futures:
                    # Nothing dispatched; avoid tight loop.
                    self._last_cycle_status = "idle"
                    time.sleep(sleep_for)
                    continue

                done, _pending = wait(
                    set(futures.keys()), timeout=max(1.0, sleep_for), return_when=FIRST_COMPLETED
                )
                for fut in done:
                    task = futures.pop(fut, None)
                    if task is None:
                        continue

                    self.mark_done(task.row_id)
                    try:
                        result = fut.result() or {}
                    except Exception as e:
                        result = {
                            "status": "failed",
                            "error": str(e),
                            "inserted": 0,
                            "attempted": 0,
                        }

                    total_inserted += int(result.get("inserted") or 0)
                    total_attempted += int(result.get("attempted") or 0)

                    logger.info(
                        "ingestion.task_done: row=%s type=%s feed=%s status=%s attempted=%s inserted=%s",
                        task.row_id,
                        task.source_type,
                        task.feed_name,
                        result.get("status"),
                        result.get("attempted"),
                        result.get("inserted"),
                    )

        return {
            "status": self._last_cycle_status,
            "day_utc": self.day_utc.isoformat(),
            "total_attempted": total_attempted,
            "total_inserted": total_inserted,
            "scheduler": self.stats().__dict__,
        }
