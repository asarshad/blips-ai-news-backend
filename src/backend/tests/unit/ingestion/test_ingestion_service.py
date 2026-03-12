from app.ingestion import service as ingestion_service


def test_run_video_discovery_ingestion_uses_pipeline(monkeypatch):
    calls = []

    class _FakePipeline:
        def ingest_video_discovery_candidates(self):
            calls.append("called")
            return {"status": "ok", "videos_ingested": 2, "reels_ingested": 1}

    monkeypatch.setattr(ingestion_service, "create_ingestion_pipeline", lambda db: _FakePipeline())

    result = ingestion_service.run_video_discovery_ingestion(db=object())

    assert calls == ["called"]
    assert result["videos_ingested"] == 2
    assert result["reels_ingested"] == 1
