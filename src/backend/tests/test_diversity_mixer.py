"""
Tests for the Diversity Mixer module.

Verifies:
- No back-to-back items from same source
- Rolling window source caps
- Rolling window topic caps (optional)
- Constraint relaxation logic
- Edge cases (small inventory, single source)
"""

import pytest
from typing import List, Dict, Any

from app.services.diversity_mixer import (
    DiversityMixer,
    MixerResult,
    create_mixer_for_surface,
    mix_feed
)
from app.config.diversity import (
    DiversityConstraints,
    DiversitySettings,
    reset_diversity_settings
)


# ============================================================================
# Test Fixtures
# ============================================================================

def make_item(
    id: int,
    source: str,
    topics: List[str] = None,
    score: float = 1.0
) -> Dict[str, Any]:
    """Create a mock content item."""
    return {
        "id": id,
        "source": source,
        "topics": topics or ["Tech"],
        "title": f"Item {id} from {source}",
        "score": score
    }


def make_items_from_sources(sources: List[str]) -> List[Dict[str, Any]]:
    """Create items with given source sequence."""
    return [
        make_item(id=i, source=src)
        for i, src in enumerate(sources, start=1)
    ]


def get_source_sequence(items: List[Dict[str, Any]]) -> List[str]:
    """Extract source sequence from items."""
    return [item["source"] for item in items]


# ============================================================================
# Basic Constraint Tests
# ============================================================================

class TestNoConsecutiveSameSource:
    """Test that no consecutive items have the same source."""
    
    def test_basic_interleaving(self):
        """Items from same source should be interleaved."""
        # Input: A, A, A, B, B, B
        items = make_items_from_sources(["A", "A", "A", "B", "B", "B"])
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=2,
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=3
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=6)
        
        sources = get_source_sequence(result.items)
        
        # Check no consecutive same source
        for i in range(len(sources) - 1):
            assert sources[i] != sources[i + 1], \
                f"Consecutive same source at position {i}: {sources}"
    
    def test_three_sources(self):
        """Should handle three sources without consecutive same."""
        items = make_items_from_sources([
            "Verge", "Verge", "Verge",
            "TC", "TC",
            "Wired", "Wired"
        ])
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=2,
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=3
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=7)
        
        sources = get_source_sequence(result.items)
        
        for i in range(len(sources) - 1):
            assert sources[i] != sources[i + 1], \
                f"Consecutive same source: {sources}"
    
    def test_allows_consecutive_when_configured(self):
        """Should allow consecutive when constraint is disabled."""
        items = make_items_from_sources(["A", "A", "B", "B"])
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=3,
            allow_consecutive_same_source=True,  # Allow consecutive
            min_inventory_for_constraints=2
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=4)
        
        # Should preserve original order when consecutive allowed
        sources = get_source_sequence(result.items)
        assert sources == ["A", "A", "B", "B"]


class TestWindowSourceCap:
    """Test rolling window source cap enforcement."""
    
    def test_window_cap_enforced(self):
        """Source cap within window should be respected when inventory allows."""
        # Create balanced inventory: 4 sources, 4 items each = 16 items
        # This allows max_source=2 in window=5 to be satisfied
        items = (
            [make_item(i, "A") for i in range(1, 5)] +
            [make_item(i, "B") for i in range(5, 9)] +
            [make_item(i, "C") for i in range(9, 13)] +
            [make_item(i, "D") for i in range(13, 17)]
        )
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=2,
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=5
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=10)
        
        sources = get_source_sequence(result.items)
        
        # Check any window of 5 has at most 2 from same source
        for i in range(len(sources) - 4):
            window = sources[i:i + 5]
            for src in set(window):
                count = window.count(src)
                assert count <= 2, \
                    f"Source {src} appears {count} times in window {i}: {window}"
    
    def test_stricter_cap_for_reels(self):
        """Reels should have stricter source cap (max 1 per window of 4).
        
        With max_source=1 in window=4, we need at least 4 sources.
        """
        # 4 sources with enough items each for window=4, max=1
        items = (
            [make_item(i, "Verge") for i in range(1, 6)] +
            [make_item(i, "MKBHD") for i in range(6, 11)] +
            [make_item(i, "LTT") for i in range(11, 16)] +
            [make_item(i, "Wired") for i in range(16, 21)]
        )
        
        constraints = DiversityConstraints(
            window_size=4,
            max_source_per_window=1,  # Stricter - need 4+ sources
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=5
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=12)
        
        sources = get_source_sequence(result.items)
        
        # Any window of 4 should have unique sources
        for i in range(len(sources) - 3):
            window = sources[i:i + 4]
            for src in set(window):
                count = window.count(src)
                assert count <= 1, \
                    f"Source {src} appears {count} times in window {i}: {window}"


class TestWindowTopicCap:
    """Test rolling window topic cap enforcement."""
    
    def test_topic_cap_enforced(self):
        """Topic cap within window should be respected."""
        items = [
            make_item(1, "A", topics=["AI"]),
            make_item(2, "B", topics=["AI"]),
            make_item(3, "C", topics=["AI"]),
            make_item(4, "D", topics=["AI"]),
            make_item(5, "E", topics=["Gaming"]),
            make_item(6, "F", topics=["Gaming"]),
            make_item(7, "G", topics=["Mobile"]),
        ]
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=3,
            max_topic_per_window=2,  # Only 2 AI per window
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=3
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=7)
        
        # Extract topics
        topics = [item["topics"][0] for item in result.items]
        
        # Check any window of 5 has at most 2 of same topic
        for i in range(len(topics) - 4):
            window = topics[i:i + 5]
            for topic in set(window):
                count = window.count(topic)
                assert count <= 2, \
                    f"Topic {topic} appears {count} times in window {i}: {window}"
    
    def test_no_topic_cap_when_none(self):
        """Should not enforce topic cap when set to None."""
        items = [
            make_item(i, chr(65 + i), topics=["AI"])
            for i in range(10)
        ]
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=3,
            max_topic_per_window=None,  # No topic cap
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=3
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=10)
        
        # Should output all items (all have same topic but that's ok)
        assert len(result.items) == 10


# ============================================================================
# Relaxation Tests
# ============================================================================

class TestConstraintRelaxation:
    """Test graceful constraint relaxation."""
    
    def test_relaxation_when_stuck(self):
        """Should relax constraints when no valid candidate found."""
        # All items from same source - impossible to satisfy strict constraints
        items = [make_item(i, "A") for i in range(10)]
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=2,
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=3,
            relaxation_steps=[
                {"allow_consecutive": True},
                {"max_source_per_window": 5},
                {"disable_all": True}
            ]
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=10)
        
        # Should relax and still return items
        assert len(result.items) == 10
        assert result.constraints_relaxed is True
        assert result.relaxation_level > 0
    
    def test_progressive_relaxation(self):
        """Relaxation should be progressive through steps."""
        # Hard to satisfy: 8 from A, 2 from B
        items = (
            [make_item(i, "A") for i in range(1, 9)] +
            [make_item(i, "B") for i in range(9, 11)]
        )
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=1,  # Very strict
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=3,
            relaxation_steps=[
                {"allow_consecutive": True},
                {"max_source_per_window": 2},
                {"max_source_per_window": 3},
                {"disable_all": True}
            ]
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=10)
        
        # Should complete with some relaxation
        assert len(result.items) == 10
        assert result.constraints_relaxed is True
    
    def test_no_relaxation_when_not_needed(self):
        """Should not relax when constraints can be satisfied."""
        items = make_items_from_sources([
            "A", "B", "C", "D",
            "A", "B", "C", "D"
        ])
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=2,
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=3
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=8)
        
        # Should not need relaxation
        assert result.constraints_relaxed is False
        assert result.relaxation_level == 0


# ============================================================================
# Edge Case Tests
# ============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_empty_input(self):
        """Should handle empty input gracefully."""
        constraints = DiversityConstraints()
        mixer = DiversityMixer(constraints)
        result = mixer.mix([], target_size=10)
        
        assert result.items == []
        assert result.constraints_relaxed is False
    
    def test_single_item(self):
        """Should handle single item input."""
        items = [make_item(1, "A")]
        
        constraints = DiversityConstraints(min_inventory_for_constraints=3)
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=5)
        
        assert len(result.items) == 1
    
    def test_small_inventory_skips_constraints(self):
        """Should skip constraints when inventory below threshold."""
        items = [make_item(i, "A") for i in range(3)]
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=2,
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=10  # Higher than input size
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=3)
        
        # Should skip constraints and return original order
        assert len(result.items) == 3
        assert result.constraints_relaxed is True
    
    def test_target_size_larger_than_input(self):
        """Should handle target size larger than input."""
        items = [make_item(i, chr(65 + i)) for i in range(5)]
        
        constraints = DiversityConstraints(min_inventory_for_constraints=3)
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=10)
        
        # Should return all available items
        assert len(result.items) == 5
    
    def test_single_source_inventory(self):
        """Should handle inventory with only one source."""
        items = [make_item(i, "TheVerge") for i in range(20)]
        
        constraints = DiversityConstraints(min_inventory_for_constraints=3)
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=10)
        
        # Should relax and return items
        assert len(result.items) == 10


# ============================================================================
# Order Preservation Tests
# ============================================================================

class TestOrderPreservation:
    """Test that relevance order is preserved when possible."""
    
    def test_preserves_order_with_diverse_input(self):
        """Should preserve order when input is already diverse."""
        items = make_items_from_sources(["A", "B", "C", "D", "E"])
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=2,
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=3
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=5)
        
        # Order should be fully preserved
        assert result.original_order_preserved == 1.0
        sources = get_source_sequence(result.items)
        assert sources == ["A", "B", "C", "D", "E"]
    
    def test_order_preservation_metric(self):
        """Order preservation metric should decrease with reordering."""
        # Input requires reordering
        items = make_items_from_sources(["A", "A", "A", "B", "B"])
        
        constraints = DiversityConstraints(
            window_size=5,
            max_source_per_window=2,
            allow_consecutive_same_source=False,
            min_inventory_for_constraints=3
        )
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=5)
        
        # Some reordering required - metric should be less than 1
        assert 0 < result.original_order_preserved < 1.0


# ============================================================================
# Integration Tests
# ============================================================================

class TestSurfaceConfiguration:
    """Test surface-specific configuration."""
    
    def setup_method(self):
        """Reset settings before each test."""
        reset_diversity_settings()
    
    def test_reels_stricter_than_articles(self):
        """Reels should have stricter default constraints."""
        article_mixer = create_mixer_for_surface("articles")
        reel_mixer = create_mixer_for_surface("reels")
        
        # Reels should have lower max_source_per_window
        assert reel_mixer.constraints.max_source_per_window <= \
               article_mixer.constraints.max_source_per_window
    
    def test_mix_feed_convenience_function(self):
        """Test the convenience mix_feed function.
        
        For reels (window=4, max_source=1), we need at least 4 sources.
        Use empty topics to avoid topic constraint issues.
        """
        # Create items without topics to focus on source diversity
        items = [
            {"id": 1, "source": "Verge", "topics": []},
            {"id": 2, "source": "Verge", "topics": []},
            {"id": 3, "source": "TC", "topics": []},
            {"id": 4, "source": "TC", "topics": []},
            {"id": 5, "source": "Wired", "topics": []},
            {"id": 6, "source": "Wired", "topics": []},
            {"id": 7, "source": "Ars", "topics": []},
            {"id": 8, "source": "Ars", "topics": []},
        ]
        
        result = mix_feed(items, surface="reels", target_size=8)
        
        # Should return mixed items
        assert len(result) == 8
        
        # No consecutive same source
        sources = get_source_sequence(result)
        for i in range(len(sources) - 1):
            assert sources[i] != sources[i + 1], \
                f"Consecutive same source at {i}: {sources}"


# ============================================================================
# Source Distribution Tests
# ============================================================================

class TestSourceDistribution:
    """Test source distribution reporting."""
    
    def test_source_distribution_accuracy(self):
        """Source distribution should accurately count items."""
        items = make_items_from_sources([
            "A", "B", "A", "C", "B", "A"
        ])
        
        constraints = DiversityConstraints(min_inventory_for_constraints=3)
        mixer = DiversityMixer(constraints)
        result = mixer.mix(items, target_size=6)
        
        # Check distribution
        assert result.source_distribution.get("A", 0) <= 3
        assert result.source_distribution.get("B", 0) <= 2
        assert result.source_distribution.get("C", 0) <= 1


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
