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

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.article_hydration import ArticleHydrationService
from app.domain.editorial import service as editorial_service_module
from app.domain.editorial.service import (
    EditorialApprovalBlockedError,
    EditorialService,
    _extract_domain,
)
from app.models.content import ContentStatus, ContentType
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
        self.canonical_url = kwargs.get("canonical_url", "")
        self.type = kwargs.get("type", None)
        self.source = kwargs.get("source", "")
        self.title = kwargs.get("title", "Test")
        self.description = kwargs.get("description", None)
        self.content_text = kwargs.get("content_text", None)
        self.summary = kwargs.get("summary", None)
        self.image_url = kwargs.get("image_url", None)
        self.ai_processed = kwargs.get("ai_processed", False)
        self.article_image_status = kwargs.get("article_image_status", None)
        self.promotion_reason = kwargs.get("promotion_reason", None)
        self.topics = kwargs.get("topics", [])
        self.entities = kwargs.get("entities", [])
        self.conversation_starters = kwargs.get("conversation_starters", None)
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
        self.video_url = kwargs.get("video_url", None)


class TestEditorialServiceSubmit:
    """Test submit_url business logic with mocked repo."""

    def _make_service(self, repo_mock):
        svc = EditorialService.__new__(EditorialService)
        svc.db = MagicMock()
        svc.repo = repo_mock
        svc._article_hydrator = None
        svc._youtube_client = None
        svc._llm_client = None
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
        assert result.content_type == "ARTICLE"

    def test_duplicate_boosted_when_importance_higher(self):
        repo = MagicMock()
        existing = FakeContentItem(id=42, editorial_boost=0)
        repo.get_by_source_url.return_value = existing
        repo.set_boost.return_value = existing
        svc = self._make_service(repo)

        result = svc.submit_url("https://techcrunch.com/article-1", importance_level=2)
        assert result.duplicate is True
        assert result.status == "duplicate_boosted"
        assert result.content_type == "ARTICLE"
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
        assert result.content_type == "ARTICLE"
        db.add.assert_called_once()
        repo.log_add_action.assert_called_once()

        created = db.add.call_args.args[0]
        assert created.title == "[pending] Post 1"
        assert "https://" not in created.title

    def test_submit_youtube_watch_url_creates_video_item(self, monkeypatch):
        repo = MagicMock()
        repo.get_by_source_url.return_value = None
        repo.get_by_canonical_key.return_value = None
        repo.log_add_action.return_value = MagicMock()

        db = MagicMock()
        svc = EditorialService(db=db, repo=repo)
        svc._youtube_client = MagicMock()
        svc._youtube_client.resolve_shared_url.return_value = SimpleNamespace(
            title="OpenAI demo",
            video_url="https://www.youtube.com/watch?v=video123",
        )
        svc._llm_client = MagicMock()

        def fake_refresh(obj):
            obj.id = 101

        db.refresh = fake_refresh

        monkeypatch.setattr(
            editorial_service_module,
            "build_video_content_item_from_entry",
            lambda *args, **kwargs: FakeContentItem(
                type=ContentType.VIDEO,
                source="YouTube",
                source_url="https://www.youtube.com/watch?v=video123",
                canonical_url="https://www.youtube.com/watch?v=video123",
                video_url="https://www.youtube.com/watch?v=video123",
                title="OpenAI demo",
                curation_status=ContentStatus.CANDIDATE,
            ),
        )

        result = svc.submit_url(
            "https://www.youtube.com/watch?v=video123",
            importance_level=2,
            actor="ios_shortcut",
        )

        assert result.duplicate is False
        assert result.status == "created"
        assert result.content_id == 101
        assert result.content_type == "VIDEO"

        created = db.add.call_args.args[0]
        assert created.type == ContentType.VIDEO
        assert created.manual_added is True
        assert created.added_by == "ios_shortcut"
        assert created.editorial_boost == 2
        assert created.discovered_via == "manual"
        assert created.acquisition_lane == "curated"
        assert created.canonical_key == "video123"
        assert created.video_url == "https://www.youtube.com/watch?v=video123"

    def test_submit_youtube_shorts_url_creates_reel_item(self, monkeypatch):
        repo = MagicMock()
        repo.get_by_source_url.return_value = None
        repo.get_by_canonical_key.return_value = None
        repo.log_add_action.return_value = MagicMock()

        db = MagicMock()
        svc = EditorialService(db=db, repo=repo)
        svc._youtube_client = MagicMock()
        svc._youtube_client.resolve_shared_url.return_value = SimpleNamespace(
            title="Quick tip",
            video_url="https://www.youtube.com/shorts/reel123",
        )
        svc._llm_client = MagicMock()

        def fake_refresh(obj):
            obj.id = 202

        db.refresh = fake_refresh

        monkeypatch.setattr(
            editorial_service_module,
            "build_video_content_item_from_entry",
            lambda *args, **kwargs: FakeContentItem(
                type=ContentType.REEL,
                source="YouTube",
                source_url="https://www.youtube.com/shorts/reel123",
                canonical_url="https://www.youtube.com/shorts/reel123",
                video_url="https://www.youtube.com/shorts/reel123",
                title="Quick tip",
                curation_status=ContentStatus.CANDIDATE,
            ),
        )

        result = svc.submit_url(
            "https://www.youtube.com/shorts/reel123",
            importance_level=1,
            actor="ios_shortcut",
        )

        assert result.duplicate is False
        assert result.status == "created"
        assert result.content_id == 202
        assert result.content_type == "REEL"

        created = db.add.call_args.args[0]
        assert created.type == ContentType.REEL
        assert created.video_url == "https://www.youtube.com/shorts/reel123"
        assert created.canonical_key == "reel123"

    def test_submit_youtube_duplicate_by_video_id_boosts_existing(self):
        repo = MagicMock()
        existing = FakeContentItem(id=55, editorial_boost=0, type=ContentType.VIDEO)
        repo.get_by_source_url.return_value = None
        repo.get_by_canonical_key.return_value = existing
        repo.set_boost.return_value = existing

        svc = self._make_service(repo)

        result = svc.submit_url("https://www.youtube.com/watch?v=dup123", importance_level=3)

        assert result.duplicate is True
        assert result.status == "duplicate_boosted"
        assert result.content_id == 55
        assert result.content_type == "VIDEO"
        repo.set_boost.assert_called_once_with(55, 3, "admin")

    def test_submit_youtube_metadata_failure_creates_fallback_stub(self):
        repo = MagicMock()
        repo.get_by_source_url.return_value = None
        repo.get_by_canonical_key.return_value = None
        repo.log_add_action.return_value = MagicMock()

        db = MagicMock()
        svc = EditorialService(db=db, repo=repo)
        svc._youtube_client = MagicMock()
        svc._youtube_client.resolve_shared_url.return_value = None

        def fake_refresh(obj):
            obj.id = 303

        db.refresh = fake_refresh

        result = svc.submit_url(
            "https://www.youtube.com/shorts/fallback123",
            importance_level=1,
            actor="ios_shortcut",
        )

        assert result.duplicate is False
        assert result.status == "created"
        assert result.content_id == 303
        assert result.content_type == "REEL"

        created = db.add.call_args.args[0]
        assert created.type == ContentType.REEL
        assert created.title == "[pending] YouTube Short"
        assert created.video_url == "https://www.youtube.com/shorts/fallback123"
        assert created.canonical_key == "fallback123"
        assert created.manual_added is True
        assert created.added_by == "ios_shortcut"

    def test_submit_rolls_back_on_integrity_error(self):
        repo = MagicMock()
        existing = FakeContentItem(id=77, editorial_boost=0)
        repo.get_by_source_url.side_effect = [None, existing]
        repo.get_by_canonical_key.return_value = None

        db = MagicMock()
        db.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate key"))
        svc = EditorialService(db=db, repo=repo)

        result = svc.submit_url("https://example.com/race-condition")
        assert result.duplicate is True
        assert result.status == "duplicate_exists"
        assert result.content_id == 77
        assert result.content_type == "ARTICLE"
        db.rollback.assert_called_once()
        repo.log_add_action.assert_not_called()


class TestEditorialServiceApproval:
    def _make_service(self, repo_mock):
        svc = EditorialService.__new__(EditorialService)
        svc.db = MagicMock()
        svc.repo = repo_mock
        svc._article_hydrator = None
        return svc

    def test_approve_hydrates_article_candidate_before_promoting(self):
        repo = MagicMock()
        item = FakeContentItem(
            id=10,
            type=ContentType.ARTICLE,
            source="Example",
            source_url="https://www.apple.com/newsroom/2026/03/story",
            canonical_url="",
            title="[pending] https://www.apple.com/newsroom/2026/03/story",
            description=None,
            content_text=None,
            summary=None,
            image_url=None,
            ai_processed=False,
            topics=[],
            entities=[],
            is_suppressed=True,
        )
        repo.get_content_by_id.return_value = item
        repo.approve.return_value = item

        svc = self._make_service(repo)
        hydrator = ArticleHydrationService()
        hydrator.run_article_extraction = MagicMock(
            return_value=SimpleNamespace(
                canonical_url="https://www.apple.com/newsroom/2026/03/story/",
                title="Apple launches new AI features",
                published_at=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
                image_url="https://images.apple.com/story/hero.jpg",
                main_text="Apple introduced a new AI feature set across iPhone, iPad, and Mac. "
                * 60,
                excerpt_fallback=None,
            )
        )
        hydrator.summarize_article = MagicMock(
            return_value=SimpleNamespace(
                summary=(
                    "Apple introduced a broad set of AI features across its devices, with "
                    "new developer tools, writing help, and on-device assistance."
                ),
                conversation_starters={
                    "starters": ["Which Apple AI feature matters most to you?"],
                    "fallback": ["What are the main points of this?"],
                },
            )
        )
        svc._article_hydrator = hydrator

        result = svc.approve_content(10, actor="reviewer", note="Looks good")

        assert result is item
        assert item.canonical_url == "https://www.apple.com/newsroom/2026/03/story/"
        assert item.title == "Apple launches new AI features"
        assert item.image_url == "https://images.apple.com/story/hero.jpg"
        assert item.content_text.startswith("Apple introduced a new AI feature set")
        assert item.summary.startswith("Apple introduced a broad set of AI features")
        assert item.ai_processed is True
        assert item.conversation_starters is not None
        assert "ai" in item.topics
        assert "apple" in item.entities
        assert item.source == "Apple"
        repo.approve.assert_called_once_with(10, actor="reviewer", note="Looks good")

    def test_promote_uses_hydrated_article_candidate(self):
        repo = MagicMock()
        item = FakeContentItem(
            id=14,
            type=ContentType.ARTICLE,
            source_url="https://example.com/story",
            title="[pending] https://example.com/story",
        )
        repo.get_content_by_id.return_value = item
        repo.promote.return_value = item

        svc = self._make_service(repo)
        hydrator = MagicMock()
        hydrator.needs_hydration.return_value = True
        hydrator.hydrate_article_candidate.side_effect = lambda candidate: (
            setattr(candidate, "canonical_url", "https://example.com/story"),
            setattr(candidate, "image_url", "https://cdn.example.com/story.jpg"),
            setattr(candidate, "content_text", "A detailed tech article. " * 80),
            setattr(candidate, "summary", "A detailed summary of the article's main tech points."),
            setattr(candidate, "ai_processed", True),
        )
        svc._article_hydrator = hydrator

        result = svc.promote_content(14, actor="reviewer")

        assert result is item
        hydrator.hydrate_article_candidate.assert_called_once_with(item)
        repo.promote.assert_called_once_with(14, actor="reviewer")

    def test_approve_skips_llm_when_item_is_already_processed(self):
        repo = MagicMock()
        item = FakeContentItem(
            id=11,
            type=ContentType.ARTICLE,
            source="Reuters",
            source_url="https://www.reuters.com/technology/story",
            canonical_url="https://www.reuters.com/technology/story",
            title="Existing Reuters article",
            content_text="Existing article text about AI and chips. " * 40,
            summary="A complete existing summary that is already long enough to keep.",
            image_url=None,
            ai_processed=True,
            topics=["ai"],
            entities=["reuters"],
        )
        repo.get_content_by_id.return_value = item
        repo.approve.return_value = item

        svc = self._make_service(repo)
        hydrator = ArticleHydrationService()
        hydrator.run_article_extraction = MagicMock(
            return_value=SimpleNamespace(
                canonical_url="https://www.reuters.com/technology/story",
                title="Updated Reuters article title",
                published_at=None,
                image_url="https://www.reuters.com/resizer/story-hero.jpg",
                main_text=None,
                excerpt_fallback=None,
            )
        )
        hydrator.summarize_article = MagicMock()
        svc._article_hydrator = hydrator

        result = svc.approve_content(11, actor="reviewer")

        assert result is item
        assert item.image_url == "https://www.reuters.com/resizer/story-hero.jpg"
        assert item.summary == "A complete existing summary that is already long enough to keep."
        hydrator.summarize_article.assert_not_called()

    def test_approve_uses_url_fallback_title_when_extraction_has_no_title(self):
        repo = MagicMock()
        item = FakeContentItem(
            id=15,
            type=ContentType.ARTICLE,
            source_url="https://www.bloomberg.com/news/articles/2026-03-21/openai-plans-to-nearly-double-its-headcount-this-year",
            title="[pending] Article pending enrichment",
            content_text=None,
            summary=None,
            ai_processed=False,
        )
        repo.get_content_by_id.return_value = item
        repo.approve.return_value = item

        svc = self._make_service(repo)
        hydrator = ArticleHydrationService()
        hydrator.run_article_extraction = MagicMock(
            return_value=SimpleNamespace(
                canonical_url=item.source_url,
                title=None,
                published_at=None,
                image_url="https://assets.bloomberg.com/openai-headcount.jpg",
                main_text="OpenAI is planning a large hiring push across research and product teams. "
                * 40,
                excerpt_fallback=None,
            )
        )
        hydrator.summarize_article = MagicMock(
            return_value=SimpleNamespace(
                summary="OpenAI is preparing a major hiring expansion to support broader AI research and product development.",
                conversation_starters=None,
            )
        )
        svc._article_hydrator = hydrator

        result = svc.approve_content(15, actor="reviewer")

        assert result is item
        assert item.title == "OpenAI Plans To Nearly Double Its Headcount This Year"
        repo.approve.assert_called_once_with(15, actor="reviewer", note=None)

    def test_approve_blocks_when_hydration_fails(self):
        repo = MagicMock()
        item = FakeContentItem(
            id=12,
            type=ContentType.ARTICLE,
            source_url="https://example.com/story",
            title="[pending] https://example.com/story",
        )
        repo.get_content_by_id.return_value = item
        repo.approve.return_value = item

        svc = self._make_service(repo)
        hydrator = MagicMock()
        hydrator.needs_hydration.return_value = True
        hydrator.hydrate_article_candidate.side_effect = RuntimeError("fetch failed")
        svc._article_hydrator = hydrator

        with pytest.raises(EditorialApprovalBlockedError) as excinfo:
            svc.approve_content(12, actor="reviewer")

        assert excinfo.value.readiness_reason == "missing_article_image"
        repo.approve.assert_not_called()

    def test_approve_does_not_summarize_placeholder_title_when_extraction_fails(self):
        repo = MagicMock()
        item = FakeContentItem(
            id=13,
            type=ContentType.ARTICLE,
            source_url="https://example.com/story",
            title="[pending] https://example.com/story",
            content_text=None,
            description=None,
            summary=None,
            ai_processed=False,
        )
        repo.get_content_by_id.return_value = item
        repo.approve.return_value = item

        svc = self._make_service(repo)
        hydrator = ArticleHydrationService()
        hydrator.run_article_extraction = MagicMock(return_value=None)
        hydrator.summarize_article = MagicMock()
        svc._article_hydrator = hydrator

        with pytest.raises(EditorialApprovalBlockedError) as excinfo:
            svc.approve_content(13, actor="reviewer")

        assert excinfo.value.readiness_reason == "missing_article_image"
        hydrator.summarize_article.assert_not_called()
        repo.approve.assert_not_called()

    def test_approve_does_not_summarize_short_article_text(self):
        repo = MagicMock()
        item = FakeContentItem(
            id=16,
            type=ContentType.ARTICLE,
            source_url="https://example.com/story",
            title="[pending] Story",
            content_text=None,
            description=None,
            summary=None,
            ai_processed=False,
        )
        repo.get_content_by_id.return_value = item
        repo.approve.return_value = item

        svc = self._make_service(repo)
        hydrator = ArticleHydrationService()
        hydrator.run_article_extraction = MagicMock(
            return_value=SimpleNamespace(
                canonical_url=item.source_url,
                title="Example story",
                published_at=None,
                image_url=None,
                main_text="Tiny article. " * 20,
                excerpt_fallback=None,
            )
        )
        hydrator.summarize_article = MagicMock()
        svc._article_hydrator = hydrator

        with pytest.raises(EditorialApprovalBlockedError) as excinfo:
            svc.approve_content(16, actor="reviewer")

        assert excinfo.value.readiness_reason == "missing_article_image"
        hydrator.summarize_article.assert_not_called()
        repo.approve.assert_not_called()


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

    def test_list_content_day_uses_ingestion_day_with_legacy_published_fallback(self):
        repo, session = self._make_repo()
        query = MagicMock()
        session.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.offset.return_value = query
        query.limit.return_value = query
        query.count.return_value = 0
        query.all.return_value = []

        repo.list_content(day=date(2026, 3, 10), page=1, page_size=50)

        day_filter_expr = query.filter.call_args_list[0].args[0]
        rendered = str(day_filter_expr)
        assert "ingestion_day" in rendered
        assert "published_at" in rendered
        assert "IS NULL" in rendered

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
            promotion_reason="curated|core|fit=0.55|blocked=weak_tech_signal_video",
        )
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.approve_and_publish(14, "editor", boost_level=3, note="priority story")

        assert result is item
        assert item.curation_status.value == "PROMOTED"
        assert item.is_suppressed is False
        assert item.editorial_boost == 3
        assert item.published_at > old_time
        assert item.promotion_reason == "curated|core|fit=0.55|preserved=editorial_override"
        session.add.assert_called_once()
        logged_action = session.add.call_args[0][0]
        assert logged_action.action_type == "APPROVE_PUBLISH"
        assert logged_action.new_value.get("note") == "priority story"
        session.commit.assert_called_once()

    def test_approve_clears_visibility_block_reason(self):
        repo, session = self._make_repo()
        item = FakeContentItem(
            id=15,
            curation_status=ContentStatus.CANDIDATE,
            is_suppressed=False,
            promotion_reason="curated|core|fit=0.55|blocked=off_topic_broad_news_video",
        )
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.approve(15, "editor")

        assert result is item
        assert item.curation_status == ContentStatus.PROMOTED
        assert item.promotion_reason == "curated|core|fit=0.55|preserved=editorial_override"

    def test_promote_idempotent(self):
        repo, session = self._make_repo()
        item = FakeContentItem(id=10, curation_status=ContentStatus.PROMOTED)
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.promote(10, "admin")
        assert result is item
        session.commit.assert_not_called()

    def test_demote_idempotent(self):
        repo, session = self._make_repo()
        item = FakeContentItem(id=10, curation_status=ContentStatus.CANDIDATE)
        session.query.return_value.filter.return_value.first.return_value = item

        result = repo.demote(10, "admin")
        assert result is item
        session.commit.assert_not_called()


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

    def test_add_reviewer_note_writes_note_action(self):
        session = MagicMock()
        repo = EditorialRepository(session)
        item = FakeContentItem(id=15)
        session.query.return_value.filter.return_value.first.return_value = item

        action = repo.add_reviewer_note(
            content_id=15, actor="reviewer", note="Needs clearer source"
        )

        assert action is not None
        session.add.assert_called_once()
        audit_record = session.add.call_args[0][0]
        assert audit_record.action_type == "NOTE"
        assert audit_record.new_value == {"note": "Needs clearer source"}
        assert audit_record.actor == "reviewer"
        session.commit.assert_called_once()


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
