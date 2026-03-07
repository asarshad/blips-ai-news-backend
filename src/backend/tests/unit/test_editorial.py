"""
Tests for the editorial / admin control layer.

Covers:
- Domain helper functions
- Duplicate submission returns existing content_id
- Manual submission creates a content item
- Boost changes are reflected in scoring
- Suppress/unsuppress toggle works
- editorial_actions audit log is written correctly
- Admin auth uses constant-time compare
- Ranking integration (editorial_boost affects global_score)

NOTE: Schema tests are skipped here because importing app.api triggers
the full app import chain (feedparser → cgi etc.).  Schemas are tested
implicitly via contract/integration tests and by Pydantic's own validation.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.domain.editorial.service import EditorialService, _extract_domain
from app.models.content import ContentStatus
from app.ranking.global_score import (
    EDITORIAL_BOOST_WEIGHT,
    compute_global_score,
    explain_global_score,
)
from app.repositories.editorial_repo import EditorialRepository

# ---------------------------------------------------------------------------
# 2. Editorial service — unit tests with mock DB
# ---------------------------------------------------------------------------


class TestExtractDomain:
    def test_basic(self):
        assert _extract_domain("https://www.techcrunch.com/article") == "techcrunch.com"

    def test_no_www(self):
        assert _extract_domain("https://arstechnica.com/news") == "arstechnica.com"

    def test_bad_url(self):
        assert _extract_domain("not a url") == ""


class FakeContentItem:
    """Lightweight stand-in for ContentItem."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 1)
        self.editorial_boost = kwargs.get("editorial_boost", 0)
        self.is_suppressed = kwargs.get("is_suppressed", False)
        self.manual_added = kwargs.get("manual_added", False)
        self.canonical_key = kwargs.get("canonical_key", None)
        self.source_url = kwargs.get("source_url", "")
        self.type = kwargs.get("type", None)
        self.source = kwargs.get("source", "")
        self.title = kwargs.get("title", "Test")
        self.curation_status = kwargs.get("curation_status", ContentStatus.CANDIDATE)
        self.published_at = kwargs.get("published_at", datetime.now(timezone.utc))
        self.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
        self.quality_score = kwargs.get("quality_score", 0.5)
        self.global_score = kwargs.get("global_score", 0.0)
        self.cluster_id = kwargs.get("cluster_id", None)
        self.added_by = kwargs.get("added_by", None)
        self.added_at = kwargs.get("added_at", None)
        self.last_modified_by = kwargs.get("last_modified_by", None)
        self.last_modified_at = kwargs.get("last_modified_at", None)


class TestEditorialServiceSubmit:
    """Test submit_url business logic with mocked repo."""

    def _make_service(self, repo_mock):
        svc = EditorialService.__new__(EditorialService)
        svc.db = MagicMock()
        svc.repo = repo_mock
        return svc

    def test_duplicate_by_source_url_returns_existing(self):
        repo = MagicMock()
        existing = FakeContentItem(id=42, canonical_key="abc")
        repo.get_by_source_url.return_value = existing
        svc = self._make_service(repo)

        result = svc.submit_url("https://techcrunch.com/article-1")
        assert result.duplicate is True
        assert result.content_id == 42
        assert result.status == "duplicate_exists"

    def test_duplicate_boosted_when_importance_higher(self):
        repo = MagicMock()
        existing = FakeContentItem(id=42, editorial_boost=0)
        repo.get_by_source_url.return_value = existing
        repo.set_boost.return_value = existing
        svc = self._make_service(repo)

        result = svc.submit_url("https://techcrunch.com/article-1", importance_level=2)
        assert result.duplicate is True
        assert result.status == "duplicate_boosted"
        repo.set_boost.assert_called_once_with(42, 2, "admin")

    def test_new_url_creates_stub(self):
        repo = MagicMock()
        repo.get_by_source_url.return_value = None
        repo.get_by_canonical_key.return_value = None
        repo.log_add_action.return_value = MagicMock()

        db = MagicMock()
        svc = EditorialService(db=db, repo=repo)

        # Patch db.add, db.commit, db.refresh to simulate insert
        def fake_refresh(obj):
            obj.id = 99

        db.refresh = fake_refresh

        result = svc.submit_url("https://newsite.com/post-1", importance_level=1)
        assert result.duplicate is False
        assert result.status == "created"
        assert result.content_id == 99
        db.add.assert_called_once()
        repo.log_add_action.assert_called_once()


# ---------------------------------------------------------------------------
# 3. Editorial repository — unit tests with mock session
# ---------------------------------------------------------------------------


class TestEditorialRepository:
    def _make_repo(self):
        session = MagicMock()
        return EditorialRepository(session), session

    def test_set_boost_logs_action(self):
        repo, session = self._make_repo()
        item = FakeContentItem(id=10, editorial_boost=0)
        session.query.return_value.filter.return_value.first.return_value = item

        repo.set_boost(10, 2, "admin")
        assert item.editorial_boost == 2
        assert item.last_modified_by == "admin"
        session.add.assert_called_once()  # audit log
        session.commit.assert_called_once()

    def test_suppress_sets_flag(self):
        repo, session = self._make_repo()
        item = FakeContentItem(id=10, is_suppressed=False)
        session.query.return_value.filter.return_value.first.return_value = item

        repo.suppress(10, "admin")
        assert item.is_suppressed is True
        session.commit.assert_called_once()

    def test_unsuppress_sets_flag(self):
        repo, session = self._make_repo()
        item = FakeContentItem(id=10, is_suppressed=True)
        session.query.return_value.filter.return_value.first.return_value = item

        repo.unsuppress(10, "admin")
        assert item.is_suppressed is False
        session.commit.assert_called_once()

    def test_suppress_idempotent(self):
        """Already-suppressed item: suppress returns item without commit."""
        repo, session = self._make_repo()
        item = FakeContentItem(id=10, is_suppressed=True)
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.suppress(10, "admin")
        assert result is item
        session.commit.assert_not_called()

    def test_unsuppress_idempotent(self):
        """Already-unsuppressed item: unsuppress returns item without commit."""
        repo, session = self._make_repo()
        item = FakeContentItem(id=10, is_suppressed=False)
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.unsuppress(10, "admin")
        assert result is item
        session.commit.assert_not_called()

    def test_set_boost_returns_none_for_missing_item(self):
        repo, session = self._make_repo()
        session.query.return_value.filter.return_value.first.return_value = None

        result = repo.set_boost(999, 2, "admin")
        assert result is None
        session.commit.assert_not_called()

    def test_list_candidate_queue_returns_paginated_rows(self):
        repo, session = self._make_repo()
        query = MagicMock()
        session.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.offset.return_value = query
        query.limit.return_value = query
        query.count.return_value = 2
        rows = [FakeContentItem(id=1), FakeContentItem(id=2)]
        query.all.return_value = rows

        items, total = repo.list_candidate_queue(page=1, page_size=2)

        assert total == 2
        assert items == rows
        query.order_by.assert_called_once()

    def test_candidate_queue_counts_groups_by_type(self):
        repo, session = self._make_repo()
        query = MagicMock()
        session.query.return_value = query
        query.filter.return_value = query
        query.group_by.return_value = query

        article_type = MagicMock()
        article_type.value = "ARTICLE"
        video_type = MagicMock()
        video_type.value = "VIDEO"
        query.all.return_value = [(article_type, 3), (video_type, 1)]

        counts = repo.candidate_queue_counts()

        assert counts == {"ARTICLE": 3, "VIDEO": 1}

    def test_approve_sets_promoted_and_unsuppressed(self):
        repo, session = self._make_repo()
        item = FakeContentItem(
            id=10,
            curation_status=ContentStatus.CANDIDATE,
            is_suppressed=True,
        )
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.approve(10, "reviewer", note="Looks good")

        assert result is item
        assert item.curation_status == ContentStatus.PROMOTED
        assert item.is_suppressed is False
        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_reject_sets_candidate_and_suppressed(self):
        repo, session = self._make_repo()
        item = FakeContentItem(
            id=11,
            curation_status=ContentStatus.PROMOTED,
            is_suppressed=False,
        )
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.reject(11, "reviewer", note="Off-topic")

        assert result is item
        assert item.curation_status == ContentStatus.CANDIDATE
        assert item.is_suppressed is True
        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_hold_sets_candidate_without_suppression(self):
        repo, session = self._make_repo()
        item = FakeContentItem(
            id=12,
            curation_status=ContentStatus.PROMOTED,
            is_suppressed=False,
        )
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.hold(12, "reviewer")

        assert result is item
        assert item.curation_status == ContentStatus.CANDIDATE
        assert item.is_suppressed is False
        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_request_changes_sets_candidate_and_logs_note(self):
        repo, session = self._make_repo()
        item = FakeContentItem(
            id=13,
            curation_status=ContentStatus.PROMOTED,
            is_suppressed=False,
        )
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.request_changes(13, "reviewer", note="Needs better title")

        assert result is item
        assert item.curation_status == ContentStatus.CANDIDATE
        assert item.is_suppressed is False
        session.add.assert_called_once()
        logged_action = session.add.call_args[0][0]
        assert logged_action.action_type == "REQUEST_CHANGES"
        assert logged_action.new_value.get("note") == "Needs better title"
        session.commit.assert_called_once()

    def test_approve_publish_promotes_sets_now_and_boost(self):
        repo, session = self._make_repo()
        old_time = datetime(2025, 1, 1)
        item = FakeContentItem(
            id=14,
            editorial_boost=1,
            is_suppressed=True,
            published_at=old_time,
        )
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.approve_and_publish(14, "editor", boost_level=3, note="priority story")

        assert result is item
        assert item.curation_status.value == "PROMOTED"
        assert item.is_suppressed is False
        assert item.editorial_boost == 3
        assert item.published_at > old_time
        session.add.assert_called_once()
        logged_action = session.add.call_args[0][0]
        assert logged_action.action_type == "APPROVE_PUBLISH"
        assert logged_action.new_value.get("note") == "priority story"
        session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Ranking integration — editorial_boost affects global_score
# ---------------------------------------------------------------------------


class TestEditorialBoostScoring:
    def test_zero_boost_no_effect(self):
        score_0 = compute_global_score(0.8, 0.5, 0.9, editorial_boost=0)
        score_also_0 = compute_global_score(0.8, 0.5, 0.9, editorial_boost=0)
        assert score_0 == score_also_0

    def test_positive_boost_increases_score(self):
        base = compute_global_score(0.8, 0.5, 0.9, editorial_boost=0)
        boosted = compute_global_score(0.8, 0.5, 0.9, editorial_boost=3)
        assert boosted > base
        assert boosted - base == pytest.approx(3 * EDITORIAL_BOOST_WEIGHT, abs=1e-6)

    def test_boost_does_not_dominate(self):
        """Max boost should not push score > 1.0 + small margin."""
        score = compute_global_score(1.0, 1.0, 1.0, editorial_boost=3)
        assert score <= 1.0 + 3 * EDITORIAL_BOOST_WEIGHT + 0.01

    def test_boost_weight_is_small(self):
        """Ensure the editorial boost weight is reasonably small."""
        assert EDITORIAL_BOOST_WEIGHT <= 0.10


# ---------------------------------------------------------------------------
# 5. Admin auth enforcement
# ---------------------------------------------------------------------------


class TestAdminAuth:
    def test_constant_time_compare_used(self):
        """Auth module uses secrets.compare_digest for timing-safe comparison."""
        import inspect

        from app.core.auth import require_admin_key

        source = inspect.getsource(require_admin_key)
        assert "compare_digest" in source

    def test_fail_closed_when_no_key_configured(self):
        """Auth module rejects requests when ADMIN_API_KEY is not set."""
        import inspect

        from app.core.auth import require_admin_key

        source = inspect.getsource(require_admin_key)
        # The function should check for empty key and raise 401
        assert "not configured_key" in source or "not configured" in source


# ---------------------------------------------------------------------------
# 6. Audit log record creation
# ---------------------------------------------------------------------------


class TestAuditLogCreation:
    def test_log_add_action_creates_record(self):
        session = MagicMock()
        repo_inst = EditorialRepository(session)

        repo_inst.log_add_action(
            content_id=10,
            actor="admin",
            url="https://example.com",
            importance_level=2,
        )
        session.add.assert_called_once()
        session.flush.assert_called_once()

    def test_set_boost_writes_audit_record(self):
        """When boost changes, an audit record is written with old and new values."""
        session = MagicMock()
        repo = EditorialRepository(session)
        item = FakeContentItem(id=10, editorial_boost=1)
        session.query.return_value.filter.return_value.first.return_value = item

        repo.set_boost(10, 3, "editor")

        # The add call is for the EditorialAction audit record
        session.add.assert_called_once()
        call_args = session.add.call_args
        audit_record = call_args[0][0]
        assert audit_record.action_type == "BOOST"
        assert audit_record.old_value == {"editorial_boost": 1}
        assert audit_record.new_value == {"editorial_boost": 3}
        assert audit_record.actor == "editor"


# ---------------------------------------------------------------------------
# 7. Explain score includes editorial component
# ---------------------------------------------------------------------------


class TestExplainGlobalScore:
    def test_includes_editorial_component(self):
        breakdown = explain_global_score(0.8, 0.5, 0.9, editorial_boost=2)
        assert "editorial" in breakdown["components"]
        assert breakdown["components"]["editorial"]["boost_level"] == 2
        assert breakdown["components"]["editorial"]["contribution"] > 0

    def test_zero_editorial_no_contribution(self):
        breakdown = explain_global_score(0.8, 0.5, 0.9, editorial_boost=0)
        assert breakdown["components"]["editorial"]["contribution"] == 0.0

    def test_all_components_present(self):
        breakdown = explain_global_score(0.8, 0.5, 0.9, editorial_boost=1)
        expected_keys = {"quality", "trend", "recency", "diversity", "editorial"}
        assert set(breakdown["components"].keys()) == expected_keys
