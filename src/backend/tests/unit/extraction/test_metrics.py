"""Tests for app.extraction.metrics — thread-safe metrics collector."""

from app.extraction.metrics import ExtractionMetrics, _SourceHealth
from app.extraction.pipeline import ExtractionResult, ExtractionStatus, ImageStatus

# ═══════════════════════════════════════════════════════════════════════════════
# _SourceHealth
# ═══════════════════════════════════════════════════════════════════════════════


class TestSourceHealth:
    def test_empty_defaults(self):
        sh = _SourceHealth()
        assert sh.total == 0
        assert sh.fail_rate == 0.0
        assert sh.health_score == 1.0  # No data = healthy

    def test_all_successes(self):
        sh = _SourceHealth(successes=10)
        assert sh.health_score == 1.0

    def test_all_failures(self):
        sh = _SourceHealth(failures=10)
        assert sh.health_score == 0.0

    def test_mixed(self):
        sh = _SourceHealth(successes=5, fallbacks=3, failures=2)
        assert sh.total == 10
        assert sh.fail_rate == 0.2
        assert sh.health_score > 0.0
        assert sh.health_score < 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# ExtractionMetrics
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractionMetrics:
    def _make_result(
        self,
        *,
        status: ExtractionStatus = ExtractionStatus.OK,
        img_status: ImageStatus = ImageStatus.OK,
    ) -> ExtractionResult:
        return ExtractionResult(
            source_url="https://example.com",
            extraction_status=status,
            image_status=img_status,
        )

    def test_counters_start_at_zero(self):
        m = ExtractionMetrics()
        c = m.get_counters()
        assert c["extraction_ok_total"] == 0
        assert c["extraction_fallback_total"] == 0
        assert c["extraction_failed_total"] == 0

    def test_record_ok(self):
        m = ExtractionMetrics()
        m.record(self._make_result(), source_name="test-source")
        c = m.get_counters()
        assert c["extraction_ok_total"] == 1
        assert c["image_ok_total"] == 1

    def test_record_fallback(self):
        m = ExtractionMetrics()
        m.record(self._make_result(status=ExtractionStatus.FALLBACK_USED))
        c = m.get_counters()
        assert c["extraction_fallback_total"] == 1

    def test_record_failed(self):
        m = ExtractionMetrics()
        m.record(self._make_result(status=ExtractionStatus.FAILED, img_status=ImageStatus.MISSING))
        c = m.get_counters()
        assert c["extraction_failed_total"] == 1
        assert c["image_missing_total"] == 1

    def test_per_source_health(self):
        m = ExtractionMetrics()
        for _ in range(8):
            m.record(self._make_result(), source_name="good-source")
        for _ in range(2):
            m.record(
                self._make_result(status=ExtractionStatus.FAILED),
                source_name="good-source",
            )
        health = m.get_source_health()
        assert "good-source" in health
        assert health["good-source"]["total"] == 10
        assert health["good-source"]["successes"] == 8
        assert health["good-source"]["health_score"] > 0.5

    def test_samples_recorded(self):
        m = ExtractionMetrics()
        m.record(self._make_result(), source_name="s1")
        samples = m.get_samples(limit=10)
        assert len(samples) == 1
        assert samples[0]["source_name"] == "s1"

    def test_samples_limited(self):
        m = ExtractionMetrics()
        for i in range(50):
            m.record(self._make_result(), source_name=f"s{i}")
        samples = m.get_samples(limit=5)
        assert len(samples) == 5

    def test_is_source_degraded(self):
        m = ExtractionMetrics()
        # Need at least 5 samples
        for _ in range(6):
            m.record(
                self._make_result(status=ExtractionStatus.FAILED),
                source_name="bad-source",
            )
        assert m.is_source_degraded("bad-source", threshold=0.3) is True

    def test_not_degraded_with_few_samples(self):
        m = ExtractionMetrics()
        # Only 3 failures — not enough data
        for _ in range(3):
            m.record(
                self._make_result(status=ExtractionStatus.FAILED),
                source_name="new-source",
            )
        assert m.is_source_degraded("new-source") is False

    def test_unknown_source_not_degraded(self):
        m = ExtractionMetrics()
        assert m.is_source_degraded("nonexistent") is False

    def test_image_invalid_counted(self):
        m = ExtractionMetrics()
        m.record(self._make_result(img_status=ImageStatus.INVALID))
        c = m.get_counters()
        assert c["image_invalid_total"] == 1

    # ── Day rollover (new) ─────────────────────────────────────────────────

    def test_day_rollover_resets_counters(self):
        """Simulating a UTC day boundary must reset daily counters."""
        from datetime import timedelta

        m = ExtractionMetrics()
        m.record(self._make_result(status=ExtractionStatus.OK), source_name="s")

        # Pretend we're now on the next UTC day
        m._day = m._day - timedelta(days=1)

        # Next record call should trigger _maybe_reset_day
        m.record(self._make_result(status=ExtractionStatus.OK), source_name="s")

        counters = m.get_counters()
        # After rollover, today's counter starts fresh from this single record
        assert counters["extraction_ok_total"] == 1

    # ── get_samples limit > available (new) ───────────────────────────────

    def test_get_samples_limit_exceeds_available(self):
        """Requesting more samples than available must not raise."""
        m = ExtractionMetrics()
        # Record only 3 samples
        for _ in range(3):
            m.record(self._make_result(), source_name="src")
        samples = m.get_samples(limit=50)
        assert len(samples) == 3  # Returns what's available
