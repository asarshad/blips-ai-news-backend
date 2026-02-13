"""
Circuit Breaker pattern for external service calls.

Protects against cascading failures when external services (YouTube API,
RSS feeds, etc.) are down. When a service fails repeatedly, the circuit
"opens" and fast-fails subsequent requests for a cooldown period before
allowing a probe request through.

States:
    CLOSED   – Normal operation. Failures are counted.
    OPEN     – Service is considered down. Calls fast-fail.
    HALF_OPEN – After cooldown, one probe request is allowed through.
"""

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, TypeVar

from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStats:
    """Snapshot of circuit breaker metrics."""

    name: str
    state: CircuitState
    failure_count: int
    success_count: int
    last_failure_time: Optional[float]
    last_success_time: Optional[float]
    total_rejected: int


class CircuitBreaker:
    """
    Thread-safe circuit breaker for external service calls.

    Args:
        name: Identifier for the protected service (for logging).
        failure_threshold: Consecutive failures before opening circuit.
        recovery_timeout: Seconds to wait before allowing a probe request.
        success_threshold: Consecutive successes in HALF_OPEN to close.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        success_threshold: int = 2,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_successes = 0
        self._last_failure_time: Optional[float] = None
        self._last_success_time: Optional[float] = None
        self._total_rejected = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_recovery()
            return self._state

    def allow_request(self) -> bool:
        """Check whether a request should be allowed through."""
        with self._lock:
            self._check_recovery()

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.HALF_OPEN:
                return True

            # OPEN – fast-fail
            self._total_rejected += 1
            return False

    def record_success(self) -> None:
        """Record a successful external call."""
        with self._lock:
            self._check_recovery()
            self._last_success_time = time.monotonic()
            self._success_count += 1

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.success_threshold:
                    self._transition(CircuitState.CLOSED)
                    self._failure_count = 0
                    self._half_open_successes = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed external call."""
        with self._lock:
            self._check_recovery()
            self._last_failure_time = time.monotonic()
            self._failure_count += 1

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed – reopen circuit
                self._transition(CircuitState.OPEN)
                self._half_open_successes = 0
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._transition(CircuitState.OPEN)

    def get_stats(self) -> CircuitStats:
        """Return current circuit breaker metrics."""
        with self._lock:
            self._check_recovery()
            return CircuitStats(
                name=self.name,
                state=self._state,
                failure_count=self._failure_count,
                success_count=self._success_count,
                last_failure_time=self._last_failure_time,
                last_success_time=self._last_success_time,
                total_rejected=self._total_rejected,
            )

    def reset(self) -> None:
        """Manually reset circuit breaker to CLOSED."""
        with self._lock:
            self._transition(CircuitState.CLOSED)
            self._failure_count = 0
            self._half_open_successes = 0
            self._total_rejected = 0

    def _check_recovery(self) -> None:
        """Transition OPEN -> HALF_OPEN if recovery timeout elapsed."""
        if self._state != CircuitState.OPEN:
            return
        if self._last_failure_time is None:
            return
        if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
            self._transition(CircuitState.HALF_OPEN)
            self._half_open_successes = 0

    def _transition(self, new_state: CircuitState) -> None:
        old_state = self._state
        self._state = new_state
        if old_state != new_state:
            logger.info(
                "Circuit breaker '%s': %s -> %s (failures=%d)",
                self.name,
                old_state.value,
                new_state.value,
                self._failure_count,
            )


# ── Shared instances for the application ────────────────────────────────

_youtube_breaker: Optional[CircuitBreaker] = None
_rss_breaker: Optional[CircuitBreaker] = None


def get_youtube_breaker() -> CircuitBreaker:
    """Get or create the YouTube API circuit breaker (singleton)."""
    global _youtube_breaker
    if _youtube_breaker is None:
        _youtube_breaker = CircuitBreaker(
            name="youtube-api",
            failure_threshold=5,
            recovery_timeout=60,
            success_threshold=2,
        )
    return _youtube_breaker


def get_rss_breaker() -> CircuitBreaker:
    """Get or create the RSS feeds circuit breaker (singleton)."""
    global _rss_breaker
    if _rss_breaker is None:
        _rss_breaker = CircuitBreaker(
            name="rss-feeds",
            failure_threshold=5,
            recovery_timeout=120,
            success_threshold=2,
        )
    return _rss_breaker
