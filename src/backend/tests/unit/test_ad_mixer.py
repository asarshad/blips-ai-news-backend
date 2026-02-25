"""
Unit tests for the feed ad mixer (``app.services.ad_mixer``).

Covers:
- Kill switch (ADS_ENABLED=False) -> no injection
- Zero frequency -> no injection
- Canary rollout gating
- Never inject as first item
- Never place two ads back-to-back
- Respect frequency interval
- Deterministic placeholder ad IDs
- _canary_bucket deterministic hashing
"""

import importlib
import importlib.util
import pathlib as _pathlib
import sys
import types

# ---------------------------------------------------------------------------
# Import ad_mixer directly, bypassing app.services.__init__ which triggers
# heavy imports (feedparser -> cgi) that break under Python 3.13+.
# ---------------------------------------------------------------------------

_ad_mixer_path = (
    _pathlib.Path(__file__).resolve().parents[2]
    / "app"
    / "services"
    / "ad_mixer.py"
)

# Force-replace app.services with a lightweight stub package so that
# spec_from_file_location can treat ad_mixer.py as a sub-module.
_pkg = types.ModuleType("app.services")
_pkg.__path__ = [str(_ad_mixer_path.parent)]
_pkg.__package__ = "app.services"
sys.modules["app.services"] = _pkg

_spec = importlib.util.spec_from_file_location(
    "app.services.ad_mixer", str(_ad_mixer_path)
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["app.services.ad_mixer"] = _mod
_spec.loader.exec_module(_mod)

inject_ads = _mod.inject_ads
_canary_bucket = _mod._canary_bucket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_items(n):
    """Create a list of fake organic feed items."""
    return [{"id": i, "title": f"Item {i}"} for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# inject_ads tests
# ---------------------------------------------------------------------------


class TestInjectAdsKillSwitch:
    """When ads are disabled, inject_ads must return items untouched."""

    def test_disabled_returns_original_items(self):
        items = _make_items(10)
        result, count = inject_ads(items, ads_enabled=False, frequency=3)
        assert result == items
        assert count == 0

    def test_default_settings_disabled(self):
        """With no overrides, settings.ADS_ENABLED is False, so no ads."""
        items = _make_items(5)
        result, count = inject_ads(items)
        assert result == items
        assert count == 0


class TestInjectAdsFrequency:
    """Frequency controls spacing of ad injection."""

    def test_zero_frequency_returns_no_ads(self):
        items = _make_items(10)
        result, count = inject_ads(items, ads_enabled=True, frequency=0)
        assert result == items
        assert count == 0

    def test_negative_frequency_returns_no_ads(self):
        items = _make_items(10)
        result, count = inject_ads(items, ads_enabled=True, frequency=-1)
        assert result == items
        assert count == 0

    def test_frequency_of_1_injects_after_every_organic(self):
        """With freq=1, an ad follows every organic item except the first."""
        items = _make_items(4)
        result, count = inject_ads(items, ads_enabled=True, frequency=1)
        # items: [O, O+A, O+A, O+A] -> 4 organic + 3 ads = 7 total
        assert count == 3
        assert len(result) == 7
        # First item must be organic
        assert result[0]["id"] == 1
        assert "ad_id" not in result[0]

    def test_frequency_of_3(self):
        """Every 3 organic items, an ad is injected (never first)."""
        items = _make_items(9)
        result, count = inject_ads(items, ads_enabled=True, frequency=3)
        # After items 3, 6, 9 -> 3 ads injected
        assert count == 3
        assert len(result) == 12

    def test_fewer_items_than_frequency(self):
        """When there aren't enough items, no ads are injected."""
        items = _make_items(2)
        result, count = inject_ads(items, ads_enabled=True, frequency=5)
        assert count == 0
        assert len(result) == 2


class TestInjectAdsRules:
    """Structural rules: first item, back-to-back prevention."""

    def test_first_item_is_always_organic(self):
        items = _make_items(10)
        result, _ = inject_ads(items, ads_enabled=True, frequency=1)
        assert "ad_id" not in result[0]
        assert result[0]["id"] == 1

    def test_no_back_to_back_ads(self):
        """No two consecutive items in the result should both be ads."""
        items = _make_items(20)
        result, _ = inject_ads(items, ads_enabled=True, frequency=1)
        for i in range(len(result) - 1):
            is_ad_current = "ad_id" in result[i]
            is_ad_next = "ad_id" in result[i + 1]
            assert not (is_ad_current and is_ad_next), (
                f"Back-to-back ads at index {i} and {i + 1}"
            )


class TestInjectAdsPlaceholder:
    """Placeholder ad structure and determinism."""

    def test_placeholder_has_required_fields(self):
        items = _make_items(5)
        result, count = inject_ads(items, ads_enabled=True, frequency=2)
        assert count >= 1

        ad = next(item for item in result if "ad_id" in item)
        assert ad["ad_id"]
        assert ad["placement_id"] == "feed_fullpage"
        assert ad["sponsor_name"] == "Blips Sponsor"
        assert ad["label"] == "Sponsored"
        assert ad["item_type"] == "AD"
        assert "title" in ad
        assert "click_url" in ad

    def test_deterministic_ad_ids(self):
        """Same input produces same ad IDs."""
        items = _make_items(5)
        result1, _ = inject_ads(items, ads_enabled=True, frequency=2)
        result2, _ = inject_ads(items, ads_enabled=True, frequency=2)
        ads1 = [item["ad_id"] for item in result1 if "ad_id" in item]
        ads2 = [item["ad_id"] for item in result2 if "ad_id" in item]
        assert ads1 == ads2

    def test_custom_placement_id(self):
        items = _make_items(5)
        result, _ = inject_ads(
            items,
            ads_enabled=True,
            frequency=2,
            placement_id="banner_top",
        )
        ad = next(item for item in result if "ad_id" in item)
        assert ad["placement_id"] == "banner_top"


class TestInjectAdsCanary:
    """Canary rollout gating."""

    def test_canary_100_always_serves(self):
        """100 percent canary means all requests get ads."""
        items = _make_items(5)
        _, count = inject_ads(
            items,
            ads_enabled=True,
            frequency=2,
            canary_percent=100,
        )
        assert count > 0

    def test_canary_0_always_serves_when_enabled(self):
        """0 percent canary = canary disabled, all requests get ads."""
        items = _make_items(5)
        _, count = inject_ads(
            items,
            ads_enabled=True,
            frequency=2,
            canary_percent=0,
        )
        assert count > 0

    def test_canary_deterministic_bucketing(self):
        """Same fingerprint always maps to same bucket."""
        bucket1 = _canary_bucket("user-abc-123")
        bucket2 = _canary_bucket("user-abc-123")
        assert bucket1 == bucket2
        assert 0 <= bucket1 <= 99

    def test_canary_bucket_range(self):
        """Bucket output is always 0-99."""
        for i in range(100):
            b = _canary_bucket(f"test-{i}")
            assert 0 <= b <= 99

    def test_canary_no_fingerprint_is_random(self):
        """Without fingerprint, bucket is random (just check range)."""
        b = _canary_bucket(None)
        assert 0 <= b <= 99


class TestInjectAdsEdgeCases:
    """Edge cases: empty lists, single item."""

    def test_empty_list(self):
        result, count = inject_ads([], ads_enabled=True, frequency=2)
        assert result == []
        assert count == 0

    def test_single_item(self):
        """Single item: never inject as first -> no ads."""
        items = _make_items(1)
        result, count = inject_ads(items, ads_enabled=True, frequency=1)
        assert count == 0
        assert len(result) == 1
