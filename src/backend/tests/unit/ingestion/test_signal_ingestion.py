"""Unit tests for signal ingestion orchestrator.

Covers:
- _detect_content_type()  – URL-based content type heuristic
- _find_existing()        – multi-probe lookup against content_items
- _build_candidate_stub() – CANDIDATE stub construction
- run_signal_ingestion()  – orchestrator behaviour (stubs, dedup, cap)
"""

from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.ingestion.signal_ingestion import (
    _build_candidate_stub,
    _detect_content_type,
    _find_existing,
    run_signal_ingestion,
)
from app.ingestion.signals import SignalItem
from app.models.candidate_audit import CandidateAuditEvent
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.signal import SignalSource

# ── _detect_content_type ─────────────────────────────────────────────────────


class TestDetectContentType:
    def test_youtube_watch_url(self):
        assert _detect_content_type("https://www.youtube.com/watch?v=abc123") == ContentType.VIDEO

    def test_youtu_be_short_url(self):
        assert _detect_content_type("https://youtu.be/abc123") == ContentType.VIDEO

    def test_github_repo_url(self):
        assert _detect_content_type("https://github.com/openai/whisper") == ContentType.ARTICLE

    def test_hn_link(self):
        assert (
            _detect_content_type("https://news.ycombinator.com/item?id=12345")
            == ContentType.ARTICLE
        )

    def test_blog_url(self):
        assert _detect_content_type("https://example.com/blog/my-post") == ContentType.ARTICLE

    def test_youtube_shorts(self):
        assert _detect_content_type("https://www.youtube.com/shorts/xyz789") == ContentType.VIDEO


# ── _find_existing ────────────────────────────────────────────────────────────


class TestFindExisting:
    def _make_repo(self, *, source_url=None, canonical_url=None, canonical_key=None):
        repo = MagicMock()
        repo.get_by_source_url.return_value = source_url
        repo.get_by_canonical_url.return_value = canonical_url
        repo.get_by_canonical_key.return_value = canonical_key
        return repo

    def test_returns_none_when_nothing_found(self):
        repo = self._make_repo()
        assert _find_existing(repo, "https://example.com/article") is None

    def test_finds_by_source_url_first(self):
        item = MagicMock(spec=ContentItem)
        repo = self._make_repo(source_url=item)
        result = _find_existing(repo, "https://example.com/article")
        assert result is item
        repo.get_by_canonical_url.assert_not_called()

    def test_falls_back_to_canonical_url(self):
        item = MagicMock(spec=ContentItem)
        repo = self._make_repo(canonical_url=item)
        result = _find_existing(repo, "https://example.com/article")
        assert result is item

    def test_finds_youtube_by_video_id_probe(self):
        item = MagicMock(spec=ContentItem)
        repo = self._make_repo()
        # get_by_canonical_key first call (yt video id) returns the item
        repo.get_by_canonical_key.return_value = item
        result = _find_existing(repo, "https://www.youtube.com/watch?v=abc123")
        assert result is item

    def test_non_youtube_url_does_not_probe_video_id(self):
        repo = self._make_repo()
        _find_existing(repo, "https://example.com/article")
        # For a non-YouTube URL, probe 3 (yt video-id path) should never fire
        # since extract_youtube_video_id returns None.  Probe 4 (sha256 canonical
        # key) may fire once.  Crucially, we should NOT see a call that looks
        # like a YouTube video-ID key (short alphanumeric, not a sha256 hex).
        for call in repo.get_by_canonical_key.call_args_list:
            key = call.args[0] if call.args else call.kwargs.get("canonical_key", "")
            # All sha256 keys are 32-char hex; a yt video-id would be short
            assert len(key) != 11, f"Unexpectedly got a YouTube video-ID probe: {key}"


# ── _build_candidate_stub ─────────────────────────────────────────────────────


class TestBuildCandidateStub:
    def _make_item(
        self, source: SignalSource = SignalSource.HN_TOP, title: str = "Test"
    ) -> SignalItem:
        return SignalItem(
            raw_url="https://example.com/article",
            signal_source=source,
            raw_title=title,
            signal_score=100.0,
        )

    def test_curation_status_is_candidate(self):
        stub = _build_candidate_stub(
            "https://example.com/article",
            self._make_item(),
            ContentType.ARTICLE,
        )
        assert stub.curation_status == ContentStatus.CANDIDATE

    def test_curation_status_is_promoted_when_auto_approve_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTO_APPROVE_REVIEW_CONTENT", True)
        stub = _build_candidate_stub(
            "https://example.com/article",
            self._make_item(),
            ContentType.ARTICLE,
        )
        assert stub.curation_status == ContentStatus.PROMOTED

    def test_ai_processed_false(self):
        stub = _build_candidate_stub(
            "https://example.com/article",
            self._make_item(),
            ContentType.ARTICLE,
        )
        assert stub.ai_processed is False

    def test_discovered_via_hn(self):
        stub = _build_candidate_stub(
            "https://example.com/article",
            self._make_item(source=SignalSource.HN_TOP),
            ContentType.ARTICLE,
        )
        assert stub.discovered_via == "signal_hn"

    def test_discovered_via_hn_best(self):
        stub = _build_candidate_stub(
            "https://example.com/article",
            self._make_item(source=SignalSource.HN_BEST),
            ContentType.ARTICLE,
        )
        assert stub.discovered_via == "signal_hn"

    def test_discovered_via_github(self):
        stub = _build_candidate_stub(
            "https://github.com/owner/repo",
            self._make_item(source=SignalSource.GITHUB_TRENDING),
            ContentType.ARTICLE,
        )
        assert stub.discovered_via == "signal_github"

    def test_discovered_via_yt_trending(self):
        stub = _build_candidate_stub(
            "https://www.youtube.com/watch?v=abc",
            self._make_item(source=SignalSource.YT_TRENDING),
            ContentType.VIDEO,
        )
        assert stub.discovered_via == "signal_yt_trending"

    def test_discovered_via_discovery_leads(self):
        stub = _build_candidate_stub(
            "https://speedrun.substack.com/p/issue",
            self._make_item(source=SignalSource.DISCOVERY_LEADS),
            ContentType.ARTICLE,
        )
        assert stub.discovered_via == "signal_discovery"

    def test_video_type_sets_video_url(self):
        url = "https://www.youtube.com/watch?v=abc123"
        stub = _build_candidate_stub(url, self._make_item(), ContentType.VIDEO)
        assert stub.type == ContentType.VIDEO
        assert stub.video_url == url

    def test_signal_hits_initialised_to_1(self):
        stub = _build_candidate_stub(
            "https://example.com/article",
            self._make_item(),
            ContentType.ARTICLE,
        )
        assert stub.signal_hits == 1

    def test_candidate_provenance_fields_are_set(self):
        stub = _build_candidate_stub(
            "https://example.com/article",
            self._make_item(source=SignalSource.HN_TOP, title="From signal"),
            ContentType.ARTICLE,
        )
        assert stub.candidate_first_seen_at is not None
        assert stub.candidate_signal_source == SignalSource.HN_TOP.value
        assert stub.candidate_raw_title == "From signal"

    def test_title_truncated_to_1000_chars(self):
        long_title = "A" * 1500
        stub = _build_candidate_stub(
            "https://example.com/article",
            self._make_item(title=long_title),
            ContentType.ARTICLE,
        )
        assert len(stub.title) <= 1000

    def test_quality_score_seeded_from_domain_policy(self):
        core_stub = _build_candidate_stub(
            "https://techcrunch.com/post",
            self._make_item(),
            ContentType.ARTICLE,
        )
        discovery_stub = _build_candidate_stub(
            "https://example.com/post",
            self._make_item(),
            ContentType.ARTICLE,
        )

        assert core_stub.quality_score == 0.9
        assert discovery_stub.quality_score == 0.62


# ── run_signal_ingestion (orchestrator) ──────────────────────────────────────


def _make_signal_row(hit_count: int = 1):
    row = MagicMock()
    row.hit_count = hit_count
    row.id = 1
    return row


def _patch_orchestrator(
    *,
    hn_top=None,
    hn_best=None,
    github=None,
    yt=None,
    discovery=None,
    existing_item=None,
    signal_row=None,
):
    """Return a context-manager stack that patches all external deps."""
    signal_row = signal_row or _make_signal_row()

    mock_content_repo = MagicMock()
    mock_content_repo.get_by_source_url.return_value = existing_item
    mock_content_repo.get_by_canonical_url.return_value = None
    mock_content_repo.get_by_canonical_key.return_value = None

    mock_signal_repo = MagicMock()
    mock_signal_repo.upsert.return_value = signal_row

    mock_db = MagicMock()
    mock_db.flush.return_value = None
    mock_db.commit.return_value = None
    mock_db.add.return_value = None

    patches = [
        patch("app.ingestion.signal_ingestion.fetch_hn_top", return_value=hn_top or []),
        patch("app.ingestion.signal_ingestion.fetch_hn_best", return_value=hn_best or []),
        patch("app.ingestion.signal_ingestion.fetch_github_trending", return_value=github or []),
        patch("app.ingestion.signal_ingestion._fetch_yt_safe", return_value=yt or []),
        patch("app.ingestion.signal_ingestion.fetch_discovery_leads", return_value=discovery or []),
        patch(
            "app.ingestion.signal_ingestion.ContentItemRepository", return_value=mock_content_repo
        ),
        patch("app.ingestion.signal_ingestion.SignalURLRepository", return_value=mock_signal_repo),
        patch("app.ingestion.signal_ingestion.normalize_url", side_effect=lambda u: u),
    ]
    return mock_db, patches, mock_content_repo, mock_signal_repo


class TestRunSignalIngestion:
    def test_new_url_creates_candidate_stub(self):
        sample = SignalItem(
            raw_url="https://example.com/brand-new-article",
            signal_source=SignalSource.HN_TOP,
            raw_title="Brand New Story",
            signal_score=150.0,
        )
        mock_db, patches, _cr, _sr = _patch_orchestrator(hn_top=[sample])
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patches[7],
        ):
            result = run_signal_ingestion(mock_db)

        assert result.signal_urls_seen == 1
        assert result.signal_urls_added == 1
        assert result.stubs_created == 1
        assert result.signal_hits_bumped == 0
        audit_events = [
            c.args[0]
            for c in mock_db.add.call_args_list
            if c.args and isinstance(c.args[0], CandidateAuditEvent)
        ]
        assert any(evt.event_type == "created" for evt in audit_events)

    def test_existing_url_bumps_signal_hits(self):
        existing = MagicMock(spec=ContentItem)
        existing.signal_hits = 2
        sample = SignalItem(
            raw_url="https://existing.com/article",
            signal_source=SignalSource.HN_BEST,
            raw_title="Old Story",
            signal_score=80.0,
        )
        mock_db, patches, _cr, _sr = _patch_orchestrator(hn_top=[sample], existing_item=existing)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patches[7],
        ):
            result = run_signal_ingestion(mock_db)

        assert result.stubs_created == 0
        assert result.signal_hits_bumped == 1
        assert existing.signal_hits == 3
        audit_events = [
            c.args[0]
            for c in mock_db.add.call_args_list
            if c.args and isinstance(c.args[0], CandidateAuditEvent)
        ]
        assert any(evt.event_type == "duplicate" for evt in audit_events)

    def test_stubs_capped_at_max_stubs(self):
        items = [
            SignalItem(
                raw_url=f"https://example.com/article-{i}",
                signal_source=SignalSource.HN_TOP,
                raw_title=f"Story {i}",
                signal_score=100.0,
            )
            for i in range(5)
        ]
        mock_db, patches, _cr, _sr = _patch_orchestrator(hn_top=items)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patches[7],
        ):
            result = run_signal_ingestion(mock_db, max_stubs=3)

        assert result.stubs_created == 3
        assert result.stubs_skipped == 2

    def test_auto_approved_article_stub_is_hydrated_before_commit(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTO_APPROVE_REVIEW_CONTENT", True)
        sample = SignalItem(
            raw_url="https://example.com/brand-new-article",
            signal_source=SignalSource.HN_TOP,
            raw_title="Brand New Story",
            signal_score=150.0,
        )
        mock_db, patches, _cr, _sr = _patch_orchestrator(hn_top=[sample])
        mock_hydrator = MagicMock()
        mock_hydrator.needs_hydration.return_value = True
        real_stub = _build_candidate_stub(
            "https://example.com/brand-new-article",
            sample,
            ContentType.ARTICLE,
        )
        mock_hydrator.build_article_stub.return_value = real_stub

        def _fill_stub(stub):
            stub.canonical_url = "https://example.com/brand-new-article/"
            stub.image_url = "https://cdn.example.com/hero.jpg"
            stub.content_text = "Hydrated article text " * 50
            stub.summary = "Hydrated summary " * 6
            stub.ai_processed = True

        mock_hydrator.hydrate_article_candidate.side_effect = _fill_stub

        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patches[7],
            patch(
                "app.ingestion.signal_ingestion.ArticleHydrationService",
                return_value=mock_hydrator,
            ),
        ):
            result = run_signal_ingestion(mock_db)

        assert result.stubs_created == 1
        created_stubs = [
            c.args[0]
            for c in mock_db.add.call_args_list
            if c.args and isinstance(c.args[0], ContentItem)
        ]
        assert len(created_stubs) == 1
        stub = created_stubs[0]
        assert stub.curation_status == ContentStatus.PROMOTED
        assert stub.image_url == "https://cdn.example.com/hero.jpg"
        assert stub.summary.startswith("Hydrated summary")
        assert stub.ai_processed is True
        mock_hydrator.hydrate_article_candidate.assert_called_once_with(stub)

    def test_fetch_error_recorded_but_continues(self):
        mock_db, patches, _cr, _sr = _patch_orchestrator()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patches[7],
            patch(
                "app.ingestion.signal_ingestion.fetch_hn_top", side_effect=RuntimeError("HN down")
            ),
        ):
            result = run_signal_ingestion(mock_db)

        assert any("HN_TOP" in e for e in result.errors)

    def test_seen_count_matches_total_fetched(self):
        items_hn = [
            SignalItem(
                raw_url=f"https://a.com/{i}",
                signal_source=SignalSource.HN_TOP,
                raw_title=f"Story {i}",
                signal_score=50.0,
            )
            for i in range(3)
        ]
        items_github = [
            SignalItem(
                raw_url=f"https://github.com/r/{i}",
                signal_source=SignalSource.GITHUB_TRENDING,
                raw_title=f"Repo {i}",
                signal_score=20.0,
            )
            for i in range(2)
        ]
        mock_db, patches, _cr, _sr = _patch_orchestrator(hn_top=items_hn, github=items_github)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patches[7],
        ):
            result = run_signal_ingestion(mock_db)

        assert result.signal_urls_seen == 5

    def test_blocked_domain_is_rejected_by_policy(self):
        blocked = SignalItem(
            raw_url="https://x.com/some/thread",
            signal_source=SignalSource.HN_TOP,
            raw_title="Blocked source",
            signal_score=12.0,
        )
        mock_db, patches, _cr, _sr = _patch_orchestrator(hn_top=[blocked])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = run_signal_ingestion(mock_db)

        assert result.signal_urls_seen == 1
        assert result.domain_rejected == 1
        assert result.signal_urls_added == 0
        assert result.stubs_created == 0

    def test_discovery_feed_items_are_processed(self):
        items_discovery = [
            SignalItem(
                raw_url=f"https://speedrun.substack.com/p/post-{i}",
                signal_source=SignalSource.DISCOVERY_LEADS,
                raw_title=f"Discovery {i}",
                signal_score=42,
            )
            for i in range(2)
        ]
        mock_db, patches, _cr, _sr = _patch_orchestrator(discovery=items_discovery)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patches[7],
        ):
            result = run_signal_ingestion(mock_db)

        assert result.signal_urls_seen == 2
        assert result.stubs_created == 2
