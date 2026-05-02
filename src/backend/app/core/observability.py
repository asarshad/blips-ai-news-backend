"""
Observability module for operational monitoring.

Provides:
- Request latency tracking via middleware
- Error rate tracking
- Connection pool statistics
- Unified operational status endpoint
"""

import os
import socket
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RequestStats:
    """Tracks request statistics per endpoint."""

    count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.count if self.count > 0 else 0.0

    @property
    def error_rate(self) -> float:
        return self.error_count / self.count if self.count > 0 else 0.0


class MetricsCollector:
    """
    Collects operational metrics for monitoring.

    Thread-safe singleton for tracking request stats across the application.
    Stats are bucketed by 5-minute windows and expire after 1 hour.
    """

    _instance = None
    _lock = Lock()

    BUCKET_SIZE_MINUTES = 5
    RETENTION_MINUTES = 60

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._stats_lock = Lock()
        # Bucketed stats: {bucket_key: {endpoint: RequestStats}}
        self._stats: Dict[str, Dict[str, RequestStats]] = defaultdict(
            lambda: defaultdict(RequestStats)
        )
        self._start_time = datetime.utcnow()
        self._initialized = True

    def _get_bucket_key(self) -> str:
        """Get current time bucket key (5-minute windows)."""
        now = datetime.utcnow()
        bucket_minute = (now.minute // self.BUCKET_SIZE_MINUTES) * self.BUCKET_SIZE_MINUTES
        return now.strftime(f"%Y-%m-%d %H:{bucket_minute:02d}")

    def _cleanup_old_buckets(self):
        """Remove buckets older than retention period."""
        cutoff = datetime.utcnow() - timedelta(minutes=self.RETENTION_MINUTES)
        cutoff_key = cutoff.strftime(
            f"%Y-%m-%d %H:{(cutoff.minute // self.BUCKET_SIZE_MINUTES) * self.BUCKET_SIZE_MINUTES:02d}"
        )

        keys_to_remove = [k for k in self._stats.keys() if k < cutoff_key]
        for k in keys_to_remove:
            del self._stats[k]

    def record_request(self, endpoint: str, latency_ms: float, is_error: bool = False):
        """Record a completed request."""
        with self._stats_lock:
            self._cleanup_old_buckets()
            bucket = self._get_bucket_key()
            stats = self._stats[bucket][endpoint]
            stats.count += 1
            stats.total_latency_ms += latency_ms
            stats.max_latency_ms = max(stats.max_latency_ms, latency_ms)
            if is_error:
                stats.error_count += 1

    def get_stats_summary(self) -> Dict[str, Any]:
        """Get aggregated stats for the last hour."""
        with self._stats_lock:
            self._cleanup_old_buckets()

            # Aggregate across all buckets
            aggregated: Dict[str, RequestStats] = defaultdict(RequestStats)
            for bucket_stats in self._stats.values():
                for endpoint, stats in bucket_stats.items():
                    agg = aggregated[endpoint]
                    agg.count += stats.count
                    agg.error_count += stats.error_count
                    agg.total_latency_ms += stats.total_latency_ms
                    agg.max_latency_ms = max(agg.max_latency_ms, stats.max_latency_ms)

            total_requests = sum(s.count for s in aggregated.values())
            total_errors = sum(s.error_count for s in aggregated.values())

            # Top endpoints by request count
            top_endpoints = sorted(
                [(ep, s) for ep, s in aggregated.items()],
                key=lambda x: x[1].count,
                reverse=True,
            )[:10]

            # Slowest endpoints by avg latency
            slow_endpoints = sorted(
                [(ep, s) for ep, s in aggregated.items() if s.count >= 5],
                key=lambda x: x[1].avg_latency_ms,
                reverse=True,
            )[:10]

            return {
                "period_minutes": self.RETENTION_MINUTES,
                "total_requests": total_requests,
                "total_errors": total_errors,
                "error_rate": total_errors / total_requests if total_requests > 0 else 0,
                "uptime_seconds": (datetime.utcnow() - self._start_time).total_seconds(),
                "top_endpoints": [
                    {
                        "endpoint": ep,
                        "count": s.count,
                        "error_count": s.error_count,
                        "avg_latency_ms": round(s.avg_latency_ms, 2),
                        "max_latency_ms": round(s.max_latency_ms, 2),
                    }
                    for ep, s in top_endpoints
                ],
                "slow_endpoints": [
                    {
                        "endpoint": ep,
                        "count": s.count,
                        "avg_latency_ms": round(s.avg_latency_ms, 2),
                        "max_latency_ms": round(s.max_latency_ms, 2),
                    }
                    for ep, s in slow_endpoints
                ],
            }

    def reset(self):
        """Reset all stats (for testing)."""
        with self._stats_lock:
            self._stats.clear()
            self._start_time = datetime.utcnow()


# Singleton instance
metrics_collector = MetricsCollector()


def _current_rss_bytes() -> int | None:
    """Best-effort current resident memory usage for Linux hosts."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return int(parts[1]) * 1024
    except OSError:
        return None
    return None


def _peak_rss_bytes() -> int | None:
    """Best-effort peak resident memory usage for the current process."""
    try:
        import resource

        max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if max_rss <= 0:
            return None
        # Linux reports kibibytes; macOS reports bytes.
        return int(max_rss if sys.platform == "darwin" else max_rss * 1024)
    except Exception:
        return None


def get_process_runtime_stats() -> Dict[str, Any]:
    """Return lightweight process runtime diagnostics for ops endpoints."""
    rss_bytes = _current_rss_bytes()
    peak_rss_bytes = _peak_rss_bytes()
    uptime_seconds = round((datetime.utcnow() - metrics_collector._start_time).total_seconds(), 2)
    return {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "python_version": sys.version.split()[0],
        "uptime_seconds": uptime_seconds,
        "rss_bytes": rss_bytes,
        "rss_mb": round(rss_bytes / (1024 * 1024), 2) if rss_bytes is not None else None,
        "peak_rss_bytes": peak_rss_bytes,
        "peak_rss_mb": round(peak_rss_bytes / (1024 * 1024), 2)
        if peak_rss_bytes is not None
        else None,
    }


def _redis_health_check() -> Dict[str, Any]:
    """Check Redis connectivity."""
    try:
        from app.core.dependencies import get_redis

        redis_client = get_redis()
        redis_client.ping()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _database_health_check() -> Dict[str, Any]:
    """Check database connectivity."""
    try:
        from sqlalchemy import text

        from app.db.base import SessionLocal

        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_ingestion_health_status() -> Dict[str, Any]:
    """Get ingestion freshness and stall status."""
    try:
        from app.scheduler.tasks_health import get_ingestion_metrics

        metrics = get_ingestion_metrics()
        if metrics.get("error"):
            return {"status": "error", **metrics}
        if metrics.get("is_stalled"):
            return {"status": "stalled", **metrics}
        return {"status": "ok", **metrics}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_scheduler_status() -> Dict[str, Any]:
    """Inspect distributed scheduler visibility via the shared Redis leader lock."""
    scheduler_enabled = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    api_legacy_scheduler_enabled = (
        os.getenv("API_LEGACY_SCHEDULER_ENABLED", "false").lower()
        in {"true", "1", "yes", "on"}
    )
    ingestion_enabled = os.getenv("INGESTION_ENABLED", "true").lower() == "true"
    external_scheduler_expected = (
        os.getenv("EXTERNAL_SCHEDULER_EXPECTED", "false").lower() == "true"
    )
    lock_key = os.getenv("SCHEDULER_LEADER_LOCK_KEY", "scheduler_lock")

    status: Dict[str, Any] = {
        "mode": "legacy_api" if api_legacy_scheduler_enabled else "external_worker",
        "scheduler_enabled": scheduler_enabled,
        "api_legacy_scheduler_enabled": api_legacy_scheduler_enabled,
        "ingestion_enabled": ingestion_enabled,
        "external_scheduler_expected": external_scheduler_expected,
        "lock_key": lock_key,
    }

    try:
        from app.core.dependencies import get_redis

        redis_client = get_redis()
        raw_owner = redis_client.get(lock_key)
        lock_ttl = redis_client.ttl(lock_key)

        owner = None
        if raw_owner is not None:
            owner = (
                raw_owner.decode("utf-8", errors="replace")
                if isinstance(raw_owner, (bytes, bytearray))
                else str(raw_owner)
            )

        status.update(
            {
                "leader_lock_present": raw_owner is not None,
                "leader_lock_owner": owner,
                "leader_lock_ttl_seconds": lock_ttl if isinstance(lock_ttl, int) and lock_ttl >= 0 else None,
            }
        )

        if raw_owner is not None:
            status["status"] = "ok"
        elif scheduler_enabled:
            status["status"] = "unhealthy"
            status["detail"] = "Scheduler is enabled locally but no leader lock is present."
        elif external_scheduler_expected or ingestion_enabled:
            status["status"] = "degraded"
            status["detail"] = "Ingestion is expected externally but no scheduler leader lock is visible."
        else:
            status["status"] = "disabled"
    except Exception as e:
        status["status"] = "error"
        status["error"] = str(e)

    return status


def get_redis_pool_stats() -> Dict[str, Any]:
    """Get Redis connection pool statistics."""
    try:
        from app.core.dependencies import get_redis_pool

        pool = get_redis_pool()
        if pool is None:
            return {"status": "not_configured"}

        return {
            "status": "ok",
            "max_connections": pool.max_connections,
            "current_connections": len(pool._in_use_connections)
            if hasattr(pool, "_in_use_connections")
            else "unknown",
            "available_connections": len(pool._available_connections)
            if hasattr(pool, "_available_connections")
            else "unknown",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_db_pool_stats() -> Dict[str, Any]:
    """Get database connection pool statistics."""
    try:
        from app.db.base import engine

        pool = engine.pool

        return {
            "status": "ok",
            "size": pool.size(),
            "checkedin": pool.checkedin(),
            "checkedout": pool.checkedout(),
            "overflow": pool.overflow(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_runtime_health(*, include_ingestion_checks: bool = True) -> Dict[str, Any]:
    """Return request-safe runtime health for health checks and ops endpoints."""
    database = _database_health_check()
    redis = _redis_health_check()
    scheduler = get_scheduler_status()
    ingestion = (
        get_ingestion_health_status()
        if include_ingestion_checks
        else {
            "status": "skipped",
            "detail": "Omitted from lightweight health probe",
        }
    )

    status = "healthy"
    if database["status"] != "ok" or redis["status"] != "ok":
        status = "unhealthy"
    elif scheduler.get("status") in {"degraded", "unhealthy", "error"} or (
        include_ingestion_checks and ingestion.get("status") in {"stalled", "error"}
    ):
        status = "degraded"

    return {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {
            "database": database,
            "redis": redis,
            "scheduler": scheduler,
            "ingestion": ingestion,
        },
    }


def get_operational_status() -> Dict[str, Any]:
    """
    Get comprehensive operational status.

    Combines:
    - Request metrics (last hour)
    - Connection pool stats
    - Service health indicators
    """
    health = get_runtime_health()
    return {
        "status": health["status"],
        "timestamp": datetime.utcnow().isoformat(),
        "process": get_process_runtime_stats(),
        "request_metrics": metrics_collector.get_stats_summary(),
        "health_checks": health["checks"],
        "redis_pool": get_redis_pool_stats(),
        "db_pool": get_db_pool_stats(),
    }
