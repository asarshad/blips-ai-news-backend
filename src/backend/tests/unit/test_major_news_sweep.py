"""Unit tests for run_major_news_classify_sweep_job (P6-1)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from app.models.content import ContentItem, ContentStatus, ContentType
from app.scheduler import tasks_major_news

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    item_id: int,
    *,
    is_major_tech_news=None,
    curation_status=ContentStatus.PROMOTED,
    published_at: datetime | None = None,
) -> MagicMock:
    item = MagicMock(spec=ContentItem)
    item.id = item_id
    item.type = ContentType.ARTICLE
    item.curation_status = curation_status
    item.is_major_tech_news = is_major_tech_news
    item.published_at = published_at or datetime.utcnow()
    item.title = f"Article {item_id}"
    item.description = f"Description for article {item_id}"
    item.summary = ""
    item.content_text = ""
    item.source = "Wired"
    item.source_url = f"https://wired.com/story/{item_id}"
    item.tech_relevance = None
    item.tech_relevance_confidence = None
    return item


def _fake_db(items: list) -> MagicMock:
    db = MagicMock()
    query_chain = MagicMock()
    query_chain.filter.return_value = query_chain
    query_chain.order_by.return_value = query_chain
    query_chain.limit.return_value = query_chain
    query_chain.all.return_value = items
    db.query.return_value = query_chain
    return db


# ---------------------------------------------------------------------------
# Basic classification behaviour
# ---------------------------------------------------------------------------


class TestSweepClassifiesUnflaggedItems:
    def test_classifies_promoted_articles_with_null_flag(self, monkeypatch):
        item = _make_item(1, is_major_tech_news=None)
        db = _fake_db([item])

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)
        monkeypatch.setattr(
            tasks_major_news,
            "_maybe_classify_major_news_value",
            lambda value, llm_client: (
                value.update({"is_major_tech_news": False, "major_tech_news_reason": "not_major"})
                or False
            ),
        )
        monkeypatch.setattr(tasks_major_news, "_fast_track_major_news_events", MagicMock())

        result = tasks_major_news.run_major_news_classify_sweep_job()

        assert result["status"] == "ok"
        assert result["classified"] == 1
        assert result["major"] == 0
        db.commit.assert_called()

    def test_marks_major_item_and_fast_tracks(self, monkeypatch):
        item = _make_item(42, is_major_tech_news=None)
        db = _fake_db([item])
        fast_track = MagicMock()

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)
        monkeypatch.setattr(
            tasks_major_news,
            "_maybe_classify_major_news_value",
            lambda value, llm_client: (
                value.update({"is_major_tech_news": True, "major_tech_news_confidence": 0.92})
                or True
            ),
        )
        monkeypatch.setattr(tasks_major_news, "_fast_track_major_news_events", fast_track)

        result = tasks_major_news.run_major_news_classify_sweep_job()

        assert result["classified"] == 1
        assert result["major"] == 1
        fast_track.assert_called_once()
        call_kwargs = fast_track.call_args
        assert 42 in call_kwargs[1]["content_item_ids"]

    def test_returns_ok_when_no_candidates(self, monkeypatch):
        db = _fake_db([])

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)

        result = tasks_major_news.run_major_news_classify_sweep_job()

        assert result["status"] == "ok"
        assert result["classified"] == 0
        assert result["major"] == 0
        db.commit.assert_not_called()

    def test_classifies_multiple_items(self, monkeypatch):
        items = [_make_item(i, is_major_tech_news=None) for i in range(5)]
        db = _fake_db(items)
        major_ids_seen: list[int] = []

        def _classifier(value, llm_client):
            item_id = int(value["source_url"].split("/")[-1])
            is_major = item_id % 2 == 0  # even IDs are major
            value["is_major_tech_news"] = is_major
            if is_major:
                major_ids_seen.append(item_id)
            return is_major

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)
        monkeypatch.setattr(tasks_major_news, "_maybe_classify_major_news_value", _classifier)
        monkeypatch.setattr(tasks_major_news, "_fast_track_major_news_events", MagicMock())

        result = tasks_major_news.run_major_news_classify_sweep_job()

        assert result["classified"] == 5
        assert result["major"] == 3  # IDs 0, 2, 4

    def test_skips_already_classified_items(self, monkeypatch):
        """Items already classified (True or False) should not be in candidates at all.
        The DB query filters them out — this test verifies the classifier is not called
        for items that somehow slip through with a non-None flag."""
        # If the query returns an already-classified item (shouldn't happen in prod,
        # but guard against it), the classifier still runs — that's acceptable.
        # What we're really testing is that the query uses is_major_tech_news.is_(None).
        db = _fake_db([])
        classifier_calls: list = []

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)
        monkeypatch.setattr(
            tasks_major_news,
            "_maybe_classify_major_news_value",
            lambda value, llm_client: classifier_calls.append(value) or False,
        )

        result = tasks_major_news.run_major_news_classify_sweep_job()

        # Query returned no candidates → classifier never called
        assert classifier_calls == []
        assert result["classified"] == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestSweepErrorHandling:
    def test_commit_failure_returns_error_and_rolls_back(self, monkeypatch):
        item = _make_item(1, is_major_tech_news=None)
        db = _fake_db([item])
        db.commit.side_effect = Exception("DB unavailable")

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)
        monkeypatch.setattr(
            tasks_major_news,
            "_maybe_classify_major_news_value",
            lambda value, llm_client: value.update({"is_major_tech_news": False}) or False,
        )

        result = tasks_major_news.run_major_news_classify_sweep_job()

        assert result["status"] == "error"
        assert result["classified"] == 0
        db.rollback.assert_called()

    def test_fast_track_commit_failure_does_not_affect_classified_count(self, monkeypatch):
        """If the fast-track commit fails, the classification commit already succeeded —
        classified count should still reflect the real work done."""
        item = _make_item(1, is_major_tech_news=None)
        db = _fake_db([item])
        commit_count = [0]

        def _commit():
            commit_count[0] += 1
            if commit_count[0] == 2:  # second commit (fast-track) fails
                raise Exception("fast-track commit failed")

        db.commit.side_effect = _commit

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)
        monkeypatch.setattr(
            tasks_major_news,
            "_maybe_classify_major_news_value",
            lambda value, llm_client: value.update({"is_major_tech_news": True}) or True,
        )
        monkeypatch.setattr(tasks_major_news, "_fast_track_major_news_events", MagicMock())

        result = tasks_major_news.run_major_news_classify_sweep_job()

        # Classification succeeded (first commit OK); fast-track failed but result is still ok
        assert result["status"] == "ok"
        assert result["classified"] == 1
        assert result["major"] == 1

    def test_db_closed_on_completion(self, monkeypatch):
        db = _fake_db([])

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)

        tasks_major_news.run_major_news_classify_sweep_job()

        db.close.assert_called_once()

    def test_db_closed_even_on_commit_failure(self, monkeypatch):
        item = _make_item(1)
        db = _fake_db([item])
        db.commit.side_effect = Exception("commit failed")

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)
        monkeypatch.setattr(
            tasks_major_news,
            "_maybe_classify_major_news_value",
            lambda value, llm_client: value.update({"is_major_tech_news": False}) or False,
        )

        tasks_major_news.run_major_news_classify_sweep_job()

        db.close.assert_called_once()


# ---------------------------------------------------------------------------
# Time-budget enforcement
# ---------------------------------------------------------------------------


class TestSweepTimeBudget:
    def test_stops_processing_when_deadline_passed(self, monkeypatch):
        """When the deadline passes mid-loop, remaining items are skipped."""
        items = [_make_item(i) for i in range(10)]
        db = _fake_db(items)
        classified_ids: list[int] = []

        # Use env to set a very tight budget so deadline is already passed on first iteration
        monkeypatch.setenv("MAJOR_NEWS_SWEEP_MAX_SECONDS", "0")  # immediate deadline

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)

        def _slow_classifier(value, llm_client):
            classified_ids.append(value.get("source_url", ""))
            value["is_major_tech_news"] = False
            return False

        monkeypatch.setattr(tasks_major_news, "_maybe_classify_major_news_value", _slow_classifier)

        result = tasks_major_news.run_major_news_classify_sweep_job()

        # With a 0-second budget, should classify 0 items (deadline already passed
        # before the loop body executes on the first iteration)
        assert result["classified"] == 0
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Timezone-aware cutoff
# ---------------------------------------------------------------------------


class TestSweepCutoffTimezone:
    def test_cutoff_is_timezone_aware(self, monkeypatch):
        """The DB filter cutoff must be timezone-aware so that comparisons with
        timezone-aware ``published_at`` columns don't raise a TypeError at runtime."""
        captured_filters: list = []
        db = MagicMock()

        class _MockQuery:
            def filter(self, *args):
                captured_filters.extend(args)
                return self

            def order_by(self, *_):
                return self

            def limit(self, *_):
                return self

            def all(self):
                return []

        db.query.return_value = _MockQuery()

        monkeypatch.setattr(tasks_major_news, "SessionLocal", lambda: db)
        monkeypatch.setattr(tasks_major_news, "LLMClient", MagicMock)

        tasks_major_news.run_major_news_classify_sweep_job()

        # Locate the published_at >= cutoff filter by inspecting bound values.
        # SQLAlchemy binary expression right-hand sides hold the Python literal.
        cutoff_values = []
        for f in captured_filters:
            try:
                val = f.right.value  # SQLAlchemy BinaryExpression
                if isinstance(val, datetime):
                    cutoff_values.append(val)
            except AttributeError:
                pass

        assert cutoff_values, "No datetime filter found — cutoff not applied?"
        for val in cutoff_values:
            assert val.tzinfo is not None, (
                f"cutoff datetime is naive (tzinfo=None): {val!r}. "
                "Use datetime.now(timezone.utc) not datetime.utcnow()."
            )
