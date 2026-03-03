"""Extraction metrics: in-memory counters and per-source health scoring.

Thread-safe counters that can be read via the /metrics API.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from app.extraction.pipeline import ExtractionResult, ExtractionStatus, ImageStatus


@dataclass
class _SourceHealth:
    """Rolling health score for a single source."""

    successes: int = 0
    fallbacks: int = 0
    failures: int = 0
    image_ok: int = 0
    image_missing: int = 0
    image_invalid: int = 0
    last_updated: Optional[datetime] = None

    @property
    def total(self) -> int:
        return self.successes + self.fallbacks + self.failures

    @property
    def fail_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.failures / self.total

    @property
    def health_score(self) -> float:
        """0..1 health score. 1.0 = perfect, 0.0 = all failures."""
        if self.total == 0:
            return 1.0  # no data yet
        score = (self.successes + self.fallbacks * 0.5) / self.total
        return round(max(0.0, min(1.0, score)), 3)


class ExtractionMetrics:
    """Thread-safe extraction metrics collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._extraction_ok = 0
        self._extraction_fallback = 0
        self._extraction_failed = 0
        self._image_ok = 0
        self._image_missing = 0
        self._image_invalid = 0
        self._sources: Dict[str, _SourceHealth] = defaultdict(_SourceHealth)
        self._day: date = datetime.now(timezone.utc).date()
        self._samples: deque[Dict[str, Any]] = deque(maxlen=100)  # Last N samples for debug

    def _maybe_reset_day(self) -> None:
        """Reset daily counters if the UTC day rolled over."""
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._extraction_ok = 0
            self._extraction_fallback = 0
            self._extraction_failed = 0
            self._image_ok = 0
            self._image_missing = 0
            self._image_invalid = 0
            self._sources.clear()
            self._samples.clear()
            self._day = today

    def record(self, result: ExtractionResult, *, source_name: str = "unknown") -> None:
        """Record an extraction result."""
        with self._lock:
            self._maybe_reset_day()

            # Global counters
            if result.extraction_status == ExtractionStatus.OK:
                self._extraction_ok += 1
            elif result.extraction_status == ExtractionStatus.FALLBACK_USED:
                self._extraction_fallback += 1
            else:
                self._extraction_failed += 1

            if result.image_status == ImageStatus.OK:
                self._image_ok += 1
            elif result.image_status == ImageStatus.MISSING:
                self._image_missing += 1
            else:
                self._image_invalid += 1

            # Per-source tracking
            src = self._sources[source_name]
            src.last_updated = datetime.now(timezone.utc)
            if result.extraction_status == ExtractionStatus.OK:
                src.successes += 1
            elif result.extraction_status == ExtractionStatus.FALLBACK_USED:
                src.fallbacks += 1
            else:
                src.failures += 1

            if result.image_status == ImageStatus.OK:
                src.image_ok += 1
            elif result.image_status == ImageStatus.MISSING:
                src.image_missing += 1
            else:
                src.image_invalid += 1

            # Keep last 100 samples for debugging
            sample = {
                "source_url": result.source_url,
                "canonical_url": result.canonical_url,
                "extraction_status": result.extraction_status.value,
                "extractor_used": result.extractor_used,
                "image_status": result.image_status.value,
                "image_source": result.image_source,
                "word_count": result.word_count,
                "text_quality_score": result.text_quality_score,
                "fetch_elapsed_ms": result.fetch_elapsed_ms,
                "source_name": source_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._samples.append(sample)  # deque(maxlen=100) auto-evicts oldest

    def get_counters(self) -> Dict[str, Any]:
        """Return current counter snapshot."""
        with self._lock:
            self._maybe_reset_day()
            return {
                "day": self._day.isoformat(),
                "extraction_ok_total": self._extraction_ok,
                "extraction_fallback_total": self._extraction_fallback,
                "extraction_failed_total": self._extraction_failed,
                "image_ok_total": self._image_ok,
                "image_missing_total": self._image_missing,
                "image_invalid_total": self._image_invalid,
            }

    def get_source_health(self) -> Dict[str, Any]:
        """Return per-source health scores."""
        with self._lock:
            self._maybe_reset_day()
            sources = {}
            for name, src in self._sources.items():
                sources[name] = {
                    "total": src.total,
                    "successes": src.successes,
                    "fallbacks": src.fallbacks,
                    "failures": src.failures,
                    "fail_rate": round(src.fail_rate, 3),
                    "health_score": src.health_score,
                    "image_ok": src.image_ok,
                    "image_missing": src.image_missing,
                    "image_invalid": src.image_invalid,
                    "last_updated": src.last_updated.isoformat() if src.last_updated else None,
                }
            return sources

    def get_samples(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent extraction samples for debugging."""
        with self._lock:
            self._maybe_reset_day()
            return list(reversed(list(self._samples)[-limit:]))

    def is_source_degraded(self, source_name: str, threshold: float = 0.3) -> bool:
        """Check if a source is below the health threshold."""
        with self._lock:
            src = self._sources.get(source_name)
            if src is None or src.total < 5:
                return False  # Not enough data
            return src.health_score < threshold


# Singleton instance
extraction_metrics = ExtractionMetrics()
