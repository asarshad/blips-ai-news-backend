from datetime import datetime
from unittest.mock import MagicMock, patch

from app.clustering.service import ClusteringService
from app.models.content import ContentItem, ContentType


def _make_article(item_id: int, title: str, *, cluster_id=None, simhash=None) -> ContentItem:
    return ContentItem(
        id=item_id,
        type=ContentType.ARTICLE,
        source="Example",
        source_url=f"https://example.com/{item_id}",
        published_at=datetime.utcnow(),
        title=title,
        topics=[],
        entities=[],
        cluster_id=cluster_id,
        simhash=simhash,
    )


def test_cluster_new_item_joins_existing_cluster_on_near_duplicate_simhash():
    repo = MagicMock()
    candidate = _make_article(1, "OpenAI launches model", cluster_id="clu12345", simhash=123456)
    incoming = _make_article(2, "OpenAI launches models", simhash=123456 ^ 0b11)
    repo.get_similar_items.return_value = [candidate]

    service = ClusteringService(repo)
    with patch("app.clustering.service.compute_similarity", return_value=0.0):
        cluster_id = service.cluster_new_item(incoming)

    assert cluster_id == "clu12345"
    repo.set_cluster.assert_called_once_with(2, "clu12345", is_canonical=False)


def test_cluster_new_item_creates_cluster_when_near_duplicate_has_no_cluster_id():
    repo = MagicMock()
    candidate = _make_article(1, "Anthropic ships update", cluster_id=None, simhash=987654)
    incoming = _make_article(2, "Anthropic ships updates", simhash=987654 ^ 0b1)
    repo.get_similar_items.return_value = [candidate]

    service = ClusteringService(repo)
    with patch("app.clustering.service.compute_similarity", return_value=0.0):
        cluster_id = service.cluster_new_item(incoming)

    assert isinstance(cluster_id, str)
    assert len(cluster_id) == 8
    assert repo.set_cluster.call_count == 2
