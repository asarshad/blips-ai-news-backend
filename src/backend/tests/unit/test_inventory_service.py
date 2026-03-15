"""
Unit tests for inventory health service.

Tests health computation, caching, and threshold detection.
"""

from datetime import datetime

from app.services.inventory_service import (
    InventoryHealth,
    SourceDistribution,
    Surface,
    SurfaceHealth,
    TierCounts,
)


class TestTierCounts:
    """Test TierCounts dataclass."""

    def test_total_calculation(self):
        """Total is sum of all tiers."""
        counts = TierCounts(tier_a=10, tier_b=5, tier_c=15)
        assert counts.total == 30

    def test_empty_counts(self):
        """Zero counts work correctly."""
        counts = TierCounts(tier_a=0, tier_b=0, tier_c=0)
        assert counts.total == 0

    def test_to_dict(self):
        """Serialization includes all fields."""
        counts = TierCounts(tier_a=10, tier_b=5, tier_c=15)
        d = counts.to_dict()
        assert d["tier_a"] == 10
        assert d["tier_b"] == 5
        assert d["tier_c"] == 15
        assert d["total"] == 30


class TestSurfaceHealth:
    """Test SurfaceHealth dataclass."""

    def _make_surface_health(self, tier_a=40, tier_b=20, tier_c=140, min_fresh=30, reservoir=200):
        """Helper to create SurfaceHealth with defaults."""
        return SurfaceHealth(
            surface=Surface.ARTICLES,
            tier_counts=TierCounts(tier_a=tier_a, tier_b=tier_b, tier_c=tier_c),
            newest_item_age_seconds=3600,
            oldest_tier_a_age_seconds=86400,
            reservoir_count=tier_a + tier_b + tier_c,
            min_fresh_threshold=min_fresh,
            reservoir_threshold=reservoir,
            source_distribution=SourceDistribution(),
            recent_refresh_count=min(tier_a, 8),
            recent_refresh_threshold=8,
            refresh_window_hours=24,
            is_healthy=tier_a >= min_fresh,
            issues=[],
        )

    def test_healthy_surface(self):
        """Surface is healthy when above min_fresh."""
        health = self._make_surface_health(tier_a=40, min_fresh=30)

        assert health.is_healthy
        assert health.tier_counts.total == 200

    def test_unhealthy_surface(self):
        """Surface is unhealthy when below min_fresh."""
        health = self._make_surface_health(tier_a=10, tier_b=5, tier_c=50, min_fresh=30)

        assert not health.is_healthy


class TestInventoryHealth:
    """Test InventoryHealth dataclass."""

    def _make_surface(self, surface, tier_a, min_fresh, is_healthy=None):
        """Helper to create SurfaceHealth."""
        if is_healthy is None:
            is_healthy = tier_a >= min_fresh
        return SurfaceHealth(
            surface=surface,
            tier_counts=TierCounts(tier_a=tier_a, tier_b=10, tier_c=100),
            newest_item_age_seconds=3600,
            oldest_tier_a_age_seconds=86400,
            reservoir_count=tier_a + 110,
            min_fresh_threshold=min_fresh,
            reservoir_threshold=200,
            source_distribution=SourceDistribution(),
            recent_refresh_count=min(tier_a, 8),
            recent_refresh_threshold=8,
            refresh_window_hours=24,
            is_healthy=is_healthy,
            issues=[] if is_healthy else [f"Below min fresh: {tier_a}/{min_fresh}"],
        )

    def test_healthy_inventory(self):
        """Inventory is healthy when all surfaces are above thresholds."""
        articles = self._make_surface(Surface.ARTICLES, tier_a=40, min_fresh=30)
        videos = self._make_surface(Surface.VIDEOS, tier_a=30, min_fresh=25)
        reels = self._make_surface(Surface.REELS, tier_a=25, min_fresh=20)

        health = InventoryHealth(
            timestamp=datetime.utcnow(),
            surfaces={Surface.ARTICLES: articles, Surface.VIDEOS: videos, Surface.REELS: reels},
            is_healthy=True,
            needs_topup=False,
            topup_priority=[],
        )

        assert health.is_healthy
        assert not health.needs_topup
        assert len(health.topup_priority) == 0

    def test_unhealthy_inventory(self):
        """Inventory needs top-up when any surface is below threshold."""
        articles = self._make_surface(Surface.ARTICLES, tier_a=5, min_fresh=30, is_healthy=False)
        videos = self._make_surface(Surface.VIDEOS, tier_a=30, min_fresh=25)
        reels = self._make_surface(Surface.REELS, tier_a=25, min_fresh=20)

        health = InventoryHealth(
            timestamp=datetime.utcnow(),
            surfaces={Surface.ARTICLES: articles, Surface.VIDEOS: videos, Surface.REELS: reels},
            is_healthy=False,
            needs_topup=True,
            topup_priority=[Surface.ARTICLES],
        )

        assert not health.is_healthy
        assert health.needs_topup
        assert Surface.ARTICLES in health.topup_priority

    def test_to_dict_serialization(self):
        """InventoryHealth serializes correctly."""
        articles = self._make_surface(Surface.ARTICLES, tier_a=40, min_fresh=30)

        health = InventoryHealth(
            timestamp=datetime(2026, 2, 6, 12, 0, 0),
            surfaces={Surface.ARTICLES: articles},
            is_healthy=True,
            needs_topup=False,
            topup_priority=[],
        )

        d = health.to_dict()
        assert d["is_healthy"] is True
        assert d["needs_topup"] is False
        assert "articles" in d["surfaces"]

    def test_surface_health_serializes_recent_refresh_fields(self):
        health = TestSurfaceHealth()._make_surface_health()

        data = health.to_dict()

        assert data["recent_refresh_count"] == 8
        assert data["recent_refresh_threshold"] == 8
        assert data["refresh_window_hours"] == 24


class TestHealthCaching:
    """Test health caching behavior."""

    def test_invalidate_clears_cache(self):
        """Invalidating health cache clears the module-level cache."""
        from app.services import inventory_service
        from app.services.inventory_service import invalidate_health_cache

        # Set some cached values
        inventory_service._cached_health = "dummy"
        inventory_service._cache_timestamp = "dummy_time"

        invalidate_health_cache()

        assert inventory_service._cached_health is None
        assert inventory_service._cache_timestamp is None

    def test_invalidate_is_idempotent(self):
        """Invalidating already-empty cache is safe."""
        from app.services import inventory_service
        from app.services.inventory_service import invalidate_health_cache

        # Ensure cache is empty
        inventory_service._cached_health = None
        inventory_service._cache_timestamp = None

        # Should not raise
        invalidate_health_cache()

        assert inventory_service._cached_health is None
