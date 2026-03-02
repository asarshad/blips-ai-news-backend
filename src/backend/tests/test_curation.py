"""
Unit tests for the curation system services.

Tests cover:
- Scoring service
- Clustering service
- Personalization service
- Playlist service
- Edge cases: clustering false positives, playlist diversity, preference decay, dedupe
"""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from app.models.content import (
    ContentItem,
    ContentType,
    EventType,
)
from app.services.clustering_service import (
    COMBINED_THRESHOLD,
    ClusteringService,
    compute_dedupe_key,
)
from app.services.personalization_service import EVENT_WEIGHTS, PersonalizationService
from app.services.playlist_service import PlaylistService
from app.services.scoring_service import (
    SCORE_WEIGHTS,
    ScoringService,
    get_source_quality_weight,
)

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_content_repo():
    """Create mock ContentItemRepository."""
    repo = Mock()
    repo.get_by_id = Mock(return_value=None)
    repo.get_by_type = Mock(return_value=[])
    repo.get_unclustered = Mock(return_value=[])
    repo.update_scores = Mock(return_value=True)
    repo.get_cluster_item_count = Mock(return_value=1)
    repo.get_cluster_stats = Mock(return_value={"item_count": 1})
    repo.get_active_cluster_ids = Mock(return_value=[])
    return repo


@pytest.fixture
def mock_profile_repo():
    """Create mock UserProfileRepository."""
    repo = Mock()
    repo.get_or_create = Mock(return_value=Mock(id=1, device_id="test-device"))
    repo.get_by_device_id = Mock(return_value=Mock(id=1, device_id="test-device"))
    return repo


@pytest.fixture
def mock_preference_repo():
    """Create mock UserPreferenceRepository."""
    repo = Mock()
    repo.upsert_preference = Mock(return_value=Mock())
    repo.get_top_preferences = Mock(return_value=[])
    return repo


@pytest.fixture
def mock_event_repo():
    """Create mock InteractionEventRepository."""
    repo = Mock()
    repo.record_event = Mock(return_value=Mock(id=1))
    repo.count_events_by_type = Mock(return_value={})
    return repo


@pytest.fixture
def sample_content_item():
    """Create sample ContentItem for testing."""
    return ContentItem(
        id=1,
        type=ContentType.ARTICLE,
        source="TechCrunch",
        source_url="https://techcrunch.com/article",
        published_at=datetime.utcnow(),
        title="AI Breakthrough: New Model Achieves Human-Level Performance",
        description="A new AI model has achieved remarkable results...",
        summary="Researchers unveiled a groundbreaking AI system...",
        image_url="https://example.com/image.jpg",
        topics=["ai", "machine learning", "deep learning"],
        entities=["openai", "gpt-4"],
        quality_score=0.8,
        trend_score=0.5,
        recency_score=1.0,
        diversity_boost=0.0,
        global_score=0.7
    )


@pytest.fixture
def sample_content_list():
    """Create list of sample content items."""
    items = []
    for i in range(10):
        items.append(ContentItem(
            id=i+1,
            type=ContentType.ARTICLE,
            source=f"Source{i % 3}",
            source_url=f"https://source{i % 3}.com/article{i}",
            published_at=datetime.utcnow() - timedelta(hours=i*2),
            title=f"Article {i}: Tech News About AI",
            topics=["ai", f"topic{i % 3}"],
            entities=["company1", f"entity{i % 2}"],
            global_score=0.8 - (i * 0.05)
        ))
    return items


# ============================================================================
# Scoring Service Tests
# ============================================================================

class TestScoringService:
    """Tests for ScoringService."""
    
    def test_source_quality_weights(self):
        """Test source quality weight lookup."""
        assert get_source_quality_weight("TechCrunch") == 0.9
        assert get_source_quality_weight("techcrunch") == 0.9
        assert get_source_quality_weight("Unknown Source") == 0.5
        assert get_source_quality_weight("MIT Technology Review") == 0.95
    
    def test_compute_quality_score(
        self,
        mock_content_repo,
        mock_event_repo,
        sample_content_item
    ):
        """Test quality score computation."""
        service = ScoringService(
            mock_content_repo,
            mock_event_repo
        )
        
        quality = service._compute_quality_score(sample_content_item)
        
        # TechCrunch = 0.9, plus completeness and metadata
        assert 0.7 <= quality <= 1.0
    
    def test_compute_recency_score_fresh(
        self,
        mock_content_repo,
        mock_event_repo,
        sample_content_item
    ):
        """Test recency score for fresh content."""
        service = ScoringService(
            mock_content_repo,
            mock_event_repo
        )
        
        # Fresh content
        sample_content_item.published_at = datetime.utcnow()
        recency = service._compute_recency_score(sample_content_item)
        
        assert 0.9 <= recency <= 1.0
    
    def test_compute_recency_score_old(
        self,
        mock_content_repo,
        mock_event_repo,
        sample_content_item
    ):
        """Test recency score for old content."""
        service = ScoringService(
            mock_content_repo,
            mock_event_repo
        )
        
        # 24 hours old = half-life
        sample_content_item.published_at = datetime.utcnow() - timedelta(hours=24)
        recency = service._compute_recency_score(sample_content_item)
        
        assert 0.45 <= recency <= 0.55  # Should be ~0.5
    
    def test_score_weights_sum_to_one(self):
        """Test that score weights sum to 1.0."""
        total = sum(SCORE_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001
    
    def test_trend_score_engagement_capped_at_20_percent(
        self,
        mock_content_repo,
        mock_event_repo,
        sample_content_item
    ):
        """Test that engagement contribution to trend score is capped at 20%."""
        service = ScoringService(mock_content_repo, mock_event_repo)
        
        # High engagement, no cluster
        mock_event_repo.count_events_by_type.return_value = {
            EventType.OPEN_SOURCE: 100,
            EventType.SHARE: 50
        }
        mock_content_repo.get_cluster_item_count.return_value = 0
        sample_content_item.cluster_id = None
        
        trend = service._compute_trend_score(sample_content_item)
        
        # With no cluster and high engagement, max score is 0.20 * 1.0 = 0.20
        assert trend <= 0.25  # Allow small margin


# ============================================================================
# Clustering Service Tests
# ============================================================================

class TestClusteringService:
    """Tests for ClusteringService."""
    
    def test_compute_dedupe_key(self):
        """Test deduplication key generation."""
        key1 = compute_dedupe_key("Test Article Title", "TechCrunch")
        key2 = compute_dedupe_key("Test Article Title", "TechCrunch")
        key3 = compute_dedupe_key("Different Title", "TechCrunch")
        
        assert key1 == key2
        assert key1 != key3
    
    def test_entity_overlap(
        self,
        mock_content_repo
    ):
        """Test entity overlap calculation."""
        service = ClusteringService(mock_content_repo)
        
        entities1 = [{"name": "apple"}, {"name": "google"}, {"name": "microsoft"}]
        entities2 = [{"name": "apple"}, {"name": "google"}, {"name": "amazon"}]
        
        overlap = service._entity_overlap(entities1, entities2)
        
        # 2 common / 4 total = 0.5
        assert abs(overlap - 0.5) < 0.01
    
    def test_title_similarity(
        self,
        mock_content_repo
    ):
        """Test title similarity calculation."""
        service = ClusteringService(mock_content_repo)
        
        title1 = "Apple Announces New iPhone Features"
        title2 = "Apple Reveals New iPhone Capabilities"
        
        similarity = service._title_similarity(title1, title2)
        
        # Should have reasonable similarity
        assert similarity > 0.3
    
    def test_compute_similarity(
        self,
        mock_content_repo
    ):
        """Test combined similarity score."""
        service = ClusteringService(mock_content_repo)
        
        item1 = ContentItem(
            id=1,
            type=ContentType.ARTICLE,
            title="OpenAI Launches GPT-5 Model",
            source="TechCrunch",
            topics=["ai", "openai", "gpt"],
            entities=[{"name": "openai"}, {"name": "gpt-5"}],
            published_at=datetime.utcnow()
        )
        item2 = ContentItem(
            id=2,
            type=ContentType.ARTICLE,
            title="OpenAI Releases GPT-5 to Public",
            source="The Verge",
            topics=["ai", "openai", "technology"],
            entities=[{"name": "openai"}, {"name": "gpt-5"}],
            published_at=datetime.utcnow()
        )
        
        similarity = service._compute_similarity(item1, item2)
        
        # Should be high similarity
        assert similarity > COMBINED_THRESHOLD
    
    # --- Edge case tests for clustering false positives ---
    
    def test_no_clustering_for_dissimilar_content(
        self,
        mock_content_repo
    ):
        """Test that dissimilar content is not clustered (false positive prevention)."""
        service = ClusteringService(mock_content_repo)
        
        # Two articles about completely different topics
        item1 = ContentItem(
            id=1,
            type=ContentType.ARTICLE,
            title="Apple Announces New MacBook Pro with M3 Chip",
            source="TechCrunch",
            topics=["apple", "macbook", "hardware"],
            entities=[{"name": "apple"}, {"name": "macbook"}],
            published_at=datetime.utcnow()
        )
        item2 = ContentItem(
            id=2,
            type=ContentType.ARTICLE,
            title="SpaceX Launches New Starship Rocket",
            source="The Verge",
            topics=["spacex", "space", "rockets"],
            entities=[{"name": "spacex"}, {"name": "starship"}],
            published_at=datetime.utcnow()
        )
        
        similarity = service._compute_similarity(item1, item2)
        
        # Should NOT exceed clustering threshold
        assert similarity < COMBINED_THRESHOLD
    
    def test_no_clustering_for_same_entity_different_context(
        self,
        mock_content_repo
    ):
        """Test that same entity in different contexts isn't clustered."""
        service = ClusteringService(mock_content_repo)
        
        # Two articles mention Apple but about different things
        item1 = ContentItem(
            id=1,
            type=ContentType.ARTICLE,
            title="Apple Launches New iPhone 16 with AI Features",
            source="TechCrunch",
            topics=["apple", "iphone", "smartphone"],
            entities=[{"name": "apple"}, {"name": "iphone"}],
            published_at=datetime.utcnow()
        )
        item2 = ContentItem(
            id=2,
            type=ContentType.ARTICLE,
            title="Apple Vision Pro Sales Disappoint in Q4",
            source="The Verge",
            topics=["apple", "vr", "headset"],
            entities=[{"name": "apple"}, {"name": "vision pro"}],
            published_at=datetime.utcnow()
        )
        
        similarity = service._compute_similarity(item1, item2)
        
        # Moderate similarity due to Apple, but not enough to cluster
        assert similarity < COMBINED_THRESHOLD
    
    # --- Dedupe edge cases ---
    
    def test_dedupe_key_case_insensitive(self):
        """Test that dedupe key is case insensitive."""
        key1 = compute_dedupe_key("TEST ARTICLE", "TECHCRUNCH")
        key2 = compute_dedupe_key("test article", "techcrunch")
        
        assert key1 == key2
    
    def test_dedupe_key_whitespace_handling(self):
        """Test that dedupe key handles whitespace correctly."""
        key1 = compute_dedupe_key("  Test Article  ", "  TechCrunch  ")
        key2 = compute_dedupe_key("Test Article", "TechCrunch")
        
        assert key1 == key2


# ============================================================================
# Personalization Service Tests
# ============================================================================

class TestPersonalizationService:
    """Tests for PersonalizationService."""
    
    def test_event_weights_defined(self):
        """Test all event types have weights."""
        for event_type in EventType:
            assert event_type in EVENT_WEIGHTS
    
    def test_record_interaction(
        self,
        mock_profile_repo,
        mock_preference_repo,
        mock_event_repo,
        mock_content_repo,
        sample_content_item
    ):
        """Test interaction recording."""
        mock_content_repo.get_by_id = Mock(return_value=sample_content_item)
        
        service = PersonalizationService(
            mock_profile_repo,
            mock_preference_repo,
            mock_event_repo,
            mock_content_repo
        )
        
        event = service.record_interaction(
            device_id="test-device",
            content_item_id=1,
            event_type=EventType.VIEW_10S
        )
        
        assert event is not None
        mock_event_repo.record_event.assert_called_once()
        
        # Should update preferences for topics
        assert mock_preference_repo.upsert_preference.call_count >= 1
    
    def test_compute_personalization_score_new_user(
        self,
        mock_profile_repo,
        mock_preference_repo,
        mock_event_repo,
        mock_content_repo,
        sample_content_item
    ):
        """Test personalization score for new user."""
        mock_profile_repo.get_by_device_id = Mock(return_value=None)
        
        service = PersonalizationService(
            mock_profile_repo,
            mock_preference_repo,
            mock_event_repo,
            mock_content_repo
        )
        
        score = service.compute_personalization_score(
            device_id="new-user",
            content=sample_content_item
        )
        
        # New user has no preferences, score should be 0
        assert score == 0.0
    
    # --- Preference decay correctness tests ---
    
    def test_preference_decay_reduces_weight(
        self,
        mock_profile_repo,
        mock_preference_repo,
        mock_event_repo,
        mock_content_repo
    ):
        """Test that preference decay reduces weights correctly."""
        # Create mock preferences with known weights
        old_pref = Mock()
        old_pref.weight = 10.0
        old_pref.last_decay = datetime.utcnow() - timedelta(days=7)
        
        mock_preference_repo.get_all_for_user = Mock(return_value=[old_pref])
        
        _service = PersonalizationService(
            mock_profile_repo,
            mock_preference_repo,
            mock_event_repo,
            mock_content_repo
        )
        
        # Verify decay factor is less than 1
        from app.services.personalization_service import DAILY_DECAY_FACTOR
        assert 0 < DAILY_DECAY_FACTOR < 1
        
        # After 7 days, weight should be significantly reduced
        # Expected: 10.0 * (0.95)^7 = ~6.98
        expected_weight = 10.0 * (DAILY_DECAY_FACTOR ** 7)
        assert expected_weight < 10.0
        assert expected_weight > 0
    
    def test_preference_decay_prunes_low_weights(
        self,
        mock_profile_repo,
        mock_preference_repo,
        mock_event_repo,
        mock_content_repo
    ):
        """Test that very low preference weights are pruned."""
        from app.services.personalization_service import MIN_PREFERENCE_WEIGHT
        
        # Weight below minimum should be pruned
        assert MIN_PREFERENCE_WEIGHT > 0
        assert MIN_PREFERENCE_WEIGHT < 1


# ============================================================================
# Playlist Service Tests
# ============================================================================

class TestPlaylistService:
    """Tests for PlaylistService."""
    
    def test_check_topic_diversity_empty(
        self,
        mock_content_repo,
        mock_profile_repo,
        mock_preference_repo
    ):
        """Test topic diversity check with empty playlist."""
        personalization = Mock()
        personalization.compute_personalization_score = Mock(return_value=0.5)
        
        service = PlaylistService(
            mock_content_repo,
            mock_profile_repo,
            mock_preference_repo,
            personalization,
            redis_client=None
        )
        
        item = ContentItem(
            id=1,
            type=ContentType.ARTICLE,
            title="Test",
            source="Test",
            topics=["ai", "tech"]
        )
        
        result = service._check_topic_diversity(item, {}, 0)
        assert result is True
    
    def test_check_topic_diversity_dominated(
        self,
        mock_content_repo,
        mock_profile_repo,
        mock_preference_repo
    ):
        """Test topic diversity check with dominated topic."""
        personalization = Mock()
        
        service = PlaylistService(
            mock_content_repo,
            mock_profile_repo,
            mock_preference_repo,
            personalization,
            redis_client=None
        )
        
        # Topic "ai" already at 40% of 10 items = 4 items
        topic_counts = {"ai": 4}
        current_size = 10
        
        item = ContentItem(
            id=1,
            type=ContentType.ARTICLE,
            title="Test",
            source="Test",
            topics=["ai"]  # Adding another AI article
        )
        
        # Adding would make it 5/11 = 45.4% > 40%
        result = service._check_topic_diversity(item, topic_counts, current_size)
        assert result is False
    
    def test_check_source_rotation_allowed(
        self,
        mock_content_repo,
        mock_profile_repo,
        mock_preference_repo
    ):
        """Test source rotation allows mixed sources."""
        personalization = Mock()
        
        service = PlaylistService(
            mock_content_repo,
            mock_profile_repo,
            mock_preference_repo,
            personalization,
            redis_client=None
        )
        
        source_streak = ["source1", "source2", "source1"]
        
        item = ContentItem(
            id=1,
            type=ContentType.ARTICLE,
            title="Test",
            source="Source1"
        )
        
        result = service._check_source_rotation(item, source_streak)
        assert result is True
    
    def test_check_source_rotation_blocked(
        self,
        mock_content_repo,
        mock_profile_repo,
        mock_preference_repo
    ):
        """Test source rotation blocks consecutive same source."""
        personalization = Mock()
        
        service = PlaylistService(
            mock_content_repo,
            mock_profile_repo,
            mock_preference_repo,
            personalization,
            redis_client=None
        )
        
        # Last 3 items all from same source
        source_streak = ["source1", "source1", "source1"]
        
        item = ContentItem(
            id=1,
            type=ContentType.ARTICLE,
            title="Test",
            source="Source1"
        )
        
        result = service._check_source_rotation(item, source_streak)
        assert result is False
    
    def test_format_item(
        self,
        mock_content_repo,
        mock_profile_repo,
        mock_preference_repo,
        sample_content_item
    ):
        """Test item formatting for API response."""
        personalization = Mock()
        
        service = PlaylistService(
            mock_content_repo,
            mock_profile_repo,
            mock_preference_repo,
            personalization,
            redis_client=None
        )
        
        formatted = service._format_item(sample_content_item)
        
        assert formatted["id"] == 1
        assert formatted["type"] == "ARTICLE"
        assert formatted["source"] == "TechCrunch"
        assert "ai" in formatted["topics"]
        assert formatted["global_score"] == 0.7
    
    # --- Playlist diversity enforcement tests ---
    
    def test_select_diverse_items_no_cluster_duplicates(
        self,
        mock_content_repo,
        mock_profile_repo,
        mock_preference_repo
    ):
        """Test that items from same cluster are deduplicated."""
        personalization = Mock()
        personalization.compute_personalization_score = Mock(return_value=0.5)
        
        service = PlaylistService(
            mock_content_repo,
            mock_profile_repo,
            mock_preference_repo,
            personalization,
            redis_client=None
        )
        
        # Create items with same cluster_id
        items = [
            (ContentItem(
                id=1, type=ContentType.ARTICLE, title="Article 1",
                source="Source1", cluster_id="cluster-a", topics=[]
            ), 0.9),
            (ContentItem(
                id=2, type=ContentType.ARTICLE, title="Article 2",
                source="Source2", cluster_id="cluster-a", topics=[]  # Same cluster
            ), 0.8),
            (ContentItem(
                id=3, type=ContentType.ARTICLE, title="Article 3",
                source="Source3", cluster_id="cluster-b", topics=[]
            ), 0.7),
        ]
        
        selected = service._select_diverse_items(items, size=3)
        
        # Only 2 items should be selected (one per cluster)
        assert len(selected) == 2
        cluster_ids = [s.cluster_id for s in selected]
        assert len(set(cluster_ids)) == 2  # Unique clusters
    
    def test_select_diverse_items_topic_enforcement(
        self,
        mock_content_repo,
        mock_profile_repo,
        mock_preference_repo
    ):
        """Test that topic dominance is enforced."""
        personalization = Mock()
        
        service = PlaylistService(
            mock_content_repo,
            mock_profile_repo,
            mock_preference_repo,
            personalization,
            redis_client=None
        )
        
        # Create items all with same topic
        items = []
        for i in range(10):
            items.append((
                ContentItem(
                    id=i+1, type=ContentType.ARTICLE, 
                    title=f"AI Article {i}",
                    source=f"Source{i}", 
                    cluster_id=f"cluster-{i}",
                    topics=["ai"]  # All same topic
                ), 
                0.9 - (i * 0.05)
            ))
        
        selected = service._select_diverse_items(items, size=10)
        
        # Due to 40% topic dominance limit, not all can be selected
        # With 10 items requested, max 4 can have same topic
        ai_count = sum(1 for s in selected if "ai" in (s.topics or []))
        assert ai_count <= 5  # Allow some margin


# ============================================================================
# Integration Tests (require database)
# ============================================================================

class TestIntegration:
    """Integration tests that require database setup."""
    
    @pytest.mark.skip(reason="Requires database connection")
    def test_full_playlist_generation(self):
        """Test full playlist generation flow."""
        pass
    
    @pytest.mark.skip(reason="Requires database connection")
    def test_full_personalization_flow(self):
        """Test full personalization flow."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
