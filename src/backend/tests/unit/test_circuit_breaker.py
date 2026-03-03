"""
Unit tests for the circuit breaker module.
"""

import time

from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
)


class TestCircuitBreaker:
    """Core circuit breaker state machine tests."""

    def test_starts_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        assert cb.state == CircuitState.CLOSED

    def test_allows_requests_when_closed(self):
        cb = CircuitBreaker("test")
        assert cb.allow_request() is True

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_rejects_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is False

    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(1.1)
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_allows_probe(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(1.1)
        assert cb.allow_request() is True

    def test_half_open_closes_after_successes(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=1, success_threshold=2)
        cb.record_failure()
        cb.record_failure()
        time.sleep(1.1)
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_reopens_on_failure(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(1.1)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        # Should not open — failure count was reset by success
        assert cb.state == CircuitState.CLOSED

    def test_reset_clears_state(self):
        cb = CircuitBreaker("test", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_stats_snapshot(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_success()
        cb.record_failure()
        stats = cb.get_stats()
        assert stats.name == "test"
        assert stats.state == CircuitState.CLOSED
        assert stats.success_count == 1
        assert stats.failure_count == 1
        assert stats.total_rejected == 0

    def test_tracks_total_rejected(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        cb.allow_request()  # rejected
        cb.allow_request()  # rejected
        stats = cb.get_stats()
        assert stats.total_rejected == 2
