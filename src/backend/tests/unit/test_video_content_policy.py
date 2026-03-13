from __future__ import annotations

from app.models.content import ContentType
from app.services import video_content_policy as policy


class _FakeQuery:
    def __init__(self):
        self.filters = []

    def filter(self, *criteria):
        self.filters.extend(criteria)
        return self


def test_youtube_discovery_is_disabled_when_curated_only_mode_is_on(monkeypatch):
    monkeypatch.setattr(policy.settings, "YOUTUBE_CURATED_ONLY", True)
    monkeypatch.setattr(policy.settings, "YOUTUBE_DISCOVERY_ENABLED", True)

    assert policy.youtube_discovery_enabled() is False


def test_apply_content_policy_filters_video_surfaces_in_curated_only_mode(monkeypatch):
    monkeypatch.setattr(policy.settings, "YOUTUBE_CURATED_ONLY", True)
    query = _FakeQuery()

    returned = policy.apply_content_policy(query, content_type=ContentType.VIDEO)

    assert returned is query
    assert len(query.filters) == 1


def test_apply_content_policy_leaves_articles_alone(monkeypatch):
    monkeypatch.setattr(policy.settings, "YOUTUBE_CURATED_ONLY", True)
    query = _FakeQuery()

    returned = policy.apply_content_policy(query, content_type=ContentType.ARTICLE)

    assert returned is query
    assert query.filters == []
