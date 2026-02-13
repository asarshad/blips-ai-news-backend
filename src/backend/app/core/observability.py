"""
Observability module for operational monitoring.

Provides:
- Request latency tracking via middleware
- Error rate tracking
- Connection pool statistics
- Unified operational status endpoint
"""

import time
from collections import defaultdict
from threading import Lock
from typing import Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta

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
        self._stats: Dict[str, Dict[str, RequestStats]] = defaultdict(lambda: defaultdict(RequestStats))
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
        cutoff_key = cutoff.strftime(f"%Y-%m-%d %H:{(cutoff.minute // self.BUCKET_SIZE_MINUTES) * self.BUCKET_SIZE_MINUTES:02d}")
        
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
            "current_connections": len(pool._in_use_connections) if hasattr(pool, '_in_use_connections') else "unknown",
            "available_connections": len(pool._available_connections) if hasattr(pool, '_available_connections') else "unknown",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_db_pool_stats() -> Dict[str, Any]:
    """Get database connection pool statistics."""
    try:
        from app.core.database import engine
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


def get_operational_status() -> Dict[str, Any]:
    """
    Get comprehensive operational status.
    
    Combines:
    - Request metrics (last hour)
    - Connection pool stats
    - Service health indicators
    """
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "request_metrics": metrics_collector.get_stats_summary(),
        "redis_pool": get_redis_pool_stats(),
        "db_pool": get_db_pool_stats(),
    }
