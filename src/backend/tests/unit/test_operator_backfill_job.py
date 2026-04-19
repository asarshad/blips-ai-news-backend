from scripts import operator_backfill_job


def test_operator_backfill_job_defaults_to_recent_promoted_image_backlog(monkeypatch):
    captured = {}

    class _FakeSession:
        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(operator_backfill_job, "SessionLocal", lambda: _FakeSession())

    def _fake_repair(db, **kwargs):
        captured["db"] = db
        captured["kwargs"] = kwargs
        return {"updated": 12}

    monkeypatch.setattr(operator_backfill_job, "repair_article_image_metadata", _fake_repair)

    result = operator_backfill_job.run()

    assert result["job"] == "article_image_backfill"
    assert result["promoted_only"] is True
    assert result["lookback_days"] == 7
    assert result["limit"] == 300
    assert result["readiness_reasons"] == [
        "missing_article_image",
        "awaiting_article_image_verification",
    ]
    assert result["repair_result"] == {"updated": 12}
    assert captured["kwargs"]["promoted_only"] is True
    assert captured["kwargs"]["readiness_reasons"] == (
        "missing_article_image",
        "awaiting_article_image_verification",
    )
    assert captured["closed"] is True


def test_operator_backfill_job_accepts_broader_payload(monkeypatch):
    captured = {}

    class _FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(operator_backfill_job, "SessionLocal", lambda: _FakeSession())

    def _fake_repair(db, **kwargs):
        captured["kwargs"] = kwargs
        return {"updated": 3}

    monkeypatch.setattr(operator_backfill_job, "repair_article_image_metadata", _fake_repair)

    result = operator_backfill_job.run(
        {
            "lookback_days": 14,
            "limit": 50,
            "all_statuses": True,
            "all_reasons": True,
        }
    )

    assert result["promoted_only"] is False
    assert result["readiness_reasons"] is None
    assert captured["kwargs"]["lookback_days"] == 14
    assert captured["kwargs"]["limit"] == 50
    assert captured["kwargs"]["promoted_only"] is False
    assert captured["kwargs"]["readiness_reasons"] is None


def test_operator_backfill_job_can_enqueue_pending_content_events(monkeypatch):
    captured = {}

    class _FakeSession:
        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(operator_backfill_job, "SessionLocal", lambda: _FakeSession())

    def _fake_enqueue(db, **kwargs):
        captured["db"] = db
        captured["kwargs"] = kwargs
        return {"job": "content_event_backfill", "queued": {"promotion": 3}}

    monkeypatch.setattr(
        operator_backfill_job,
        "enqueue_pending_content_events",
        _fake_enqueue,
    )

    result = operator_backfill_job.run(
        {
            "job": "content_event_backfill",
            "lookback_days": 14,
            "limit": 200,
            "pending_only": False,
        }
    )

    assert result == {"job": "content_event_backfill", "queued": {"promotion": 3}}
    assert captured["kwargs"] == {
        "lookback_days": 14,
        "limit": 200,
        "pending_only": False,
    }
    assert captured["closed"] is True
