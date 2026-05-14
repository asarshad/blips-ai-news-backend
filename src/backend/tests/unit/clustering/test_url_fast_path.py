"""Unit tests for the URL fast-path in ClusteringService.cluster_new_item (P2-5)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.clustering.service import ClusteringService
from app.models.content import ContentItem, ContentType


def _make_article(
    item_id: int,
    title: str,
    source_url: str = "",
    *,
    cluster_id: str | None = None,
) -> ContentItem:
    return ContentItem(
        id=item_id,
        type=ContentType.ARTICLE,
        source="Example",
        source_url=source_url or f"https://example.com/article-{item_id}",
        published_at=datetime.utcnow(),
        title=title,
        topics=[],
        entities=[],
        cluster_id=cluster_id,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service() -> tuple[ClusteringService, MagicMock]:
    repo = MagicMock()
    service = ClusteringService(repo)
    return service, repo


# ---------------------------------------------------------------------------
# URL fast-path: joins existing cluster
# ---------------------------------------------------------------------------


class TestUrlFastPathJoinsExistingCluster:
    def test_same_url_different_params_joins_cluster(self):
        """Candidate has a cluster; incoming URL matches after canonicalization."""
        service, repo = _make_service()

        candidate = _make_article(
            1,
            "OpenAI releases GPT-5",
            source_url="https://techcrunch.com/2024/01/gpt5-story",
            cluster_id="abc12345",
        )
        incoming = _make_article(
            2,
            "OpenAI releases GPT-5",
            source_url="https://www.techcrunch.com/2024/01/gpt5-story?utm_source=rss",
        )
        repo.get_similar_items.return_value = [candidate]

        with patch("app.clustering.service.compute_similarity") as mock_sim:
            cluster_id = service.cluster_new_item(incoming)

        # Should short-circuit before compute_similarity is called
        mock_sim.assert_not_called()
        assert cluster_id == "abc12345"
        repo.set_cluster.assert_called_once_with(2, "abc12345", is_canonical=False)

    def test_amp_subdomain_matches_canonical(self):
        """AMP subdomain URL on candidate collapses to same key as canonical incoming URL."""
        service, repo = _make_service()

        candidate = _make_article(
            1,
            "Anthropic launches Claude 4",
            source_url="https://amp.theverge.com/2024/01/claude4",
            cluster_id="xyz99999",
        )
        incoming = _make_article(
            2,
            "Anthropic launches Claude 4",
            source_url="https://theverge.com/2024/01/claude4?ref=newsletter",
        )
        repo.get_similar_items.return_value = [candidate]

        with patch("app.clustering.service.compute_similarity") as mock_sim:
            cluster_id = service.cluster_new_item(incoming)

        mock_sim.assert_not_called()
        assert cluster_id == "xyz99999"

    def test_amp_path_suffix_matches_canonical(self):
        """AMP path suffix (/amp) on incoming URL matches the canonical candidate URL."""
        service, repo = _make_service()

        candidate = _make_article(
            1,
            "Google releases Gemini Ultra",
            source_url="https://arstechnica.com/tech/2024/01/gemini-ultra",
            cluster_id="gemini1",
        )
        incoming = _make_article(
            2,
            "Google releases Gemini Ultra",
            source_url="https://arstechnica.com/tech/2024/01/gemini-ultra/amp/",
        )
        repo.get_similar_items.return_value = [candidate]

        with patch("app.clustering.service.compute_similarity") as mock_sim:
            cluster_id = service.cluster_new_item(incoming)

        mock_sim.assert_not_called()
        assert cluster_id == "gemini1"


# ---------------------------------------------------------------------------
# URL fast-path: creates new cluster
# ---------------------------------------------------------------------------


class TestUrlFastPathCreatesCluster:
    def test_same_url_no_cluster_creates_new_cluster(self):
        """Both items have no cluster; URL match → create new cluster for both."""
        service, repo = _make_service()

        candidate = _make_article(
            1,
            "OpenAI releases GPT-5",
            source_url="https://techcrunch.com/2024/01/gpt5",
            cluster_id=None,
        )
        incoming = _make_article(
            2,
            "OpenAI releases GPT-5",
            source_url="https://techcrunch.com/2024/01/gpt5?utm_campaign=social",
        )
        repo.get_similar_items.return_value = [candidate]

        with patch("app.clustering.service.compute_similarity") as mock_sim:
            cluster_id = service.cluster_new_item(incoming)

        mock_sim.assert_not_called()
        assert isinstance(cluster_id, str)
        assert len(cluster_id) == 8
        # Both items should be assigned the SAME cluster_id
        assert repo.set_cluster.call_count == 2
        call_args = [c[0] for c in repo.set_cluster.call_args_list]
        cluster_ids_used = {args[1] for args in call_args}
        assert len(cluster_ids_used) == 1, "Both items must be put in the same cluster"

    def test_prefers_candidate_with_existing_cluster(self):
        """When multiple URL matches exist, prefer the one that already has a cluster_id."""
        service, repo = _make_service()

        # candidate[0] has no cluster; candidate[1] has one
        candidate_no_cluster = _make_article(
            1,
            "OpenAI releases GPT-5",
            source_url="https://techcrunch.com/2024/01/gpt5",
            cluster_id=None,
        )
        candidate_clustered = _make_article(
            2,
            "OpenAI releases GPT-5",
            source_url="https://techcrunch.com/2024/01/gpt5?ref=email",
            cluster_id="existing-1",
        )
        incoming = _make_article(
            3,
            "OpenAI releases GPT-5",
            source_url="https://www.techcrunch.com/2024/01/gpt5?utm_source=rss",
        )
        repo.get_similar_items.return_value = [candidate_no_cluster, candidate_clustered]

        with patch("app.clustering.service.compute_similarity") as mock_sim:
            cluster_id = service.cluster_new_item(incoming)

        mock_sim.assert_not_called()
        # Should join the existing cluster, not create a new one
        assert cluster_id == "existing-1"
        repo.set_cluster.assert_called_once_with(3, "existing-1", is_canonical=False)


# ---------------------------------------------------------------------------
# URL fast-path: no match — falls through to similarity scoring
# ---------------------------------------------------------------------------


class TestUrlFastPathFallthrough:
    def test_different_paths_fall_through_to_similarity(self):
        """Different article paths → URL fast-path skipped, similarity scorer runs."""
        service, repo = _make_service()

        candidate = _make_article(
            1,
            "OpenAI releases GPT-5",
            source_url="https://techcrunch.com/2024/01/gpt5",
            cluster_id="old-cluster",
        )
        incoming = _make_article(
            2,
            "OpenAI releases GPT-5",
            source_url="https://techcrunch.com/2024/01/different-article",
        )
        repo.get_similar_items.return_value = [candidate]

        with patch("app.clustering.service.compute_similarity", return_value=0.0) as mock_sim:
            cluster_id = service.cluster_new_item(incoming)

        # Falls through to similarity scorer
        mock_sim.assert_called()
        assert cluster_id is None

    def test_no_source_url_falls_through_to_similarity(self):
        """No source_url on incoming item → fast-path skipped, similarity scorer runs."""
        service, repo = _make_service()

        candidate = _make_article(
            1,
            "Apple launches iPhone 17",
            source_url="https://techcrunch.com/2024/01/iphone17",
            cluster_id="iphone-cluster",
        )
        incoming = _make_article(
            2,
            "Apple launches iPhone 17",
            source_url="",  # no URL
        )
        repo.get_similar_items.return_value = [candidate]

        with patch("app.clustering.service.compute_similarity", return_value=0.0) as mock_sim:
            service.cluster_new_item(incoming)

        mock_sim.assert_called()

    def test_candidate_no_url_falls_through(self):
        """No source_url on candidate → cannot URL-match, falls through to similarity."""
        service, repo = _make_service()

        candidate = _make_article(
            1,
            "Apple launches iPhone 17",
            source_url="",  # no URL
            cluster_id="iphone-cluster",
        )
        incoming = _make_article(
            2,
            "Apple launches iPhone 17",
            source_url="https://techcrunch.com/2024/01/iphone17",
        )
        repo.get_similar_items.return_value = [candidate]

        with patch("app.clustering.service.compute_similarity", return_value=0.0) as mock_sim:
            service.cluster_new_item(incoming)

        mock_sim.assert_called()

    def test_no_candidates_returns_none(self):
        """No candidates at all → returns None immediately."""
        service, repo = _make_service()
        repo.get_similar_items.return_value = []
        incoming = _make_article(1, "Some article", source_url="https://example.com/story")

        with patch("app.clustering.service.compute_similarity") as mock_sim:
            cluster_id = service.cluster_new_item(incoming)

        mock_sim.assert_not_called()
        assert cluster_id is None

    def test_video_item_skips_url_fast_path(self):
        """VIDEO items must not use the URL fast-path (YouTube URLs would collapse)."""
        service, repo = _make_service()

        # Two completely unrelated videos on YouTube — same host, different ?v= param
        candidate = ContentItem(
            id=1,
            type=ContentType.VIDEO,
            source="YouTube",
            source_url="https://www.youtube.com/watch?v=AAAA1111",
            published_at=datetime.utcnow(),
            title="Unrelated video A",
            topics=[],
            entities=[],
            cluster_id="video-cluster-A",
        )
        incoming = ContentItem(
            id=2,
            type=ContentType.VIDEO,
            source="YouTube",
            source_url="https://www.youtube.com/watch?v=BBBB2222",
            published_at=datetime.utcnow(),
            title="Unrelated video B",
            topics=[],
            entities=[],
            cluster_id=None,
        )
        repo.get_similar_items.return_value = [candidate]

        with patch("app.clustering.service.compute_similarity", return_value=0.0) as mock_sim:
            cluster_id = service.cluster_new_item(incoming)

        # Must fall through to similarity scorer — NOT fast-pathed together
        mock_sim.assert_called()
        assert cluster_id is None  # similarity returned 0.0 → no cluster

    def test_none_source_url_falls_through(self):
        """source_url=None on incoming item → fast-path skipped."""
        service, repo = _make_service()

        candidate = _make_article(
            1,
            "Apple launches iPhone 17",
            source_url="https://techcrunch.com/2024/01/iphone17",
            cluster_id="iphone-cluster",
        )
        # Build item with source_url=None manually
        incoming = ContentItem(
            id=2,
            type=ContentType.ARTICLE,
            source="Example",
            source_url=None,
            published_at=datetime.utcnow(),
            title="Apple launches iPhone 17",
            topics=[],
            entities=[],
        )
        repo.get_similar_items.return_value = [candidate]

        with patch("app.clustering.service.compute_similarity", return_value=0.0) as mock_sim:
            service.cluster_new_item(incoming)

        mock_sim.assert_called()
