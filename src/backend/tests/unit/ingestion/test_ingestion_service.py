from app.ingestion import service as ingestion_service
from app.models.content import ContentType


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


def test_discovery_remaining_needed_uses_fresh_promoted_gap_for_videos():
    pipeline = ingestion_service.IngestionPipeline.__new__(ingestion_service.IngestionPipeline)
    pipeline._fresh_promoted_inventory_count = lambda content_type: 14

    remaining = pipeline._discovery_remaining_needed(
        ContentType.VIDEO,
        created_remaining=0,
    )

    assert remaining == 16


def test_discovery_remaining_needed_prefers_created_gap_when_larger():
    pipeline = ingestion_service.IngestionPipeline.__new__(ingestion_service.IngestionPipeline)
    pipeline._fresh_promoted_inventory_count = lambda content_type: 34

    remaining = pipeline._discovery_remaining_needed(
        ContentType.REEL,
        created_remaining=9,
    )

    assert remaining == 9
