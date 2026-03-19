from __future__ import annotations

import json
from pathlib import Path

from app.integrations.youtube_channels import ChannelRole
from app.integrations.youtube_client import VideoEntry
from app.tools.search_key_lab import (
    SearchKeyLabQueryResult,
    build_report,
    render_summary_markdown,
    write_artifacts,
)


class _FakeYouTubeClient:
    def __init__(self, responses):
        self._responses = responses

    def fetch_search_candidates(
        self,
        query: str,
        *,
        region_code: str = "US",
        max_results: int = 25,
        surface: str = "videos",
        search_order: str = "relevance",
        query_label: str | None = None,
        published_after=None,
    ):
        del max_results, search_order, published_after
        return list(self._responses.get((surface, query_label, region_code), []))

    def fetch_channel_stats(self, channel_ids):
        return {
            channel_id: {
                "subscriber_count": 250000,
                "video_count": 200,
                "view_count": 1000000,
            }
            for channel_id in channel_ids
        }


def _entry(
    *,
    query_label: str,
    video_id: str,
    surface: str,
    source: str,
    channel_id: str,
    default_language: str = "en",
    view_count: int = 6000,
    like_count: int = 400,
    comment_count: int = 60,
):
    return VideoEntry(
        title="OpenAI update" if surface == "videos" else "OpenAI shorts",
        video_url=f"https://youtube.com/watch?v={video_id}",
        thumbnail_url="https://img.youtube.com/vi/demo/default.jpg",
        summary="OpenAI ships an AI product update for developers.",
        source=source,
        category="Artificial Intelligence",
        video_id=video_id,
        channel_id=channel_id,
        channel_role=ChannelRole.OFFICIAL,
        is_short=surface == "reels",
        acquisition_lane="search",
        query_label=query_label,
        source_status="discovery",
        duration_seconds=45 if surface == "reels" else 480,
        view_count=view_count,
        like_count=like_count,
        comment_count=comment_count,
        views_per_hour=500.0,
        format_fit_score=1.0,
        default_language=default_language,
    )


def test_build_report_evaluates_registry_without_database(tmp_path: Path):
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
queries:
  - id: ai-video
    category: AI
    concept: OpenAI product update
    surfaces: [videos]
    mode: always_on
    youtube_fit: high
    priority: 1
    video_query: OpenAI update
    order: date
    enabled: true
  - id: ai-reel
    category: AI
    concept: OpenAI shorts
    surfaces: [reels]
    mode: always_on
    youtube_fit: high
    priority: 1
    reel_query: OpenAI shorts
    order: viewCount
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    client = _FakeYouTubeClient(
        {
            ("videos", "ai-video", "US"): [
                _entry(
                    query_label="ai-video",
                    video_id="v1",
                    surface="videos",
                    source="OpenAI",
                    channel_id="c1",
                ),
                _entry(
                    query_label="ai-video",
                    video_id="v2",
                    surface="videos",
                    source="OpenAI Developers",
                    channel_id="c2",
                ),
                _entry(
                    query_label="ai-video",
                    video_id="v3",
                    surface="videos",
                    source="OpenAI News",
                    channel_id="c3",
                ),
            ],
            ("reels", "ai-reel", "US"): [
                _entry(
                    query_label="ai-reel",
                    video_id="r1",
                    surface="reels",
                    source="OpenAI",
                    channel_id="c4",
                ),
                _entry(
                    query_label="ai-reel",
                    video_id="r2",
                    surface="reels",
                    source="OpenAI Dev",
                    channel_id="c5",
                ),
            ],
        }
    )

    report = build_report(
        registry_path=registry_path,
        surface="both",
        regions=("US",),
        query_ids=(),
        youtube_client=client,
    )

    assert report["summary"]["videos"]["accepted_candidates"] == 3
    assert report["summary"]["reels"]["accepted_candidates"] == 2
    video_result = next(item for item in report["results"] if item["query_id"] == "ai-video")
    reel_result = next(item for item in report["results"] if item["query_id"] == "ai-reel")
    assert video_result["passed_thresholds"] is True
    assert reel_result["passed_thresholds"] is True
    assert video_result["distinct_channels"] == 3
    assert reel_result["distinct_channels"] == 2


def test_build_report_rejects_non_english_heavy_query(tmp_path: Path):
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
queries:
  - id: chip-shorts
    category: Chips
    concept: NVIDIA shorts
    surfaces: [reels]
    mode: always_on
    youtube_fit: high
    priority: 2
    reel_query: NVIDIA shorts
    order: viewCount
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    client = _FakeYouTubeClient(
        {
            ("reels", "chip-shorts", "US"): [
                _entry(
                    query_label="chip-shorts",
                    video_id="r1",
                    surface="reels",
                    source="NVIDIA",
                    channel_id="c1",
                    default_language="es",
                ),
                _entry(
                    query_label="chip-shorts",
                    video_id="r2",
                    surface="reels",
                    source="NVIDIA AI",
                    channel_id="c2",
                    default_language="es",
                ),
                _entry(
                    query_label="chip-shorts",
                    video_id="r3",
                    surface="reels",
                    source="NVIDIA",
                    channel_id="c3",
                ),
            ]
        }
    )

    report = build_report(
        registry_path=registry_path,
        surface="reels",
        regions=("US",),
        query_ids=(),
        youtube_client=client,
    )

    result = report["results"][0]
    assert result["raw_candidates"] == 3
    assert result["filtered_non_english"] == 2
    assert result["passed_thresholds"] is False


def test_write_artifacts_outputs_results_summary_and_best_registry(tmp_path: Path):
    report = {
        "generated_at": "2026-03-18T12:00:00",
        "registry_path": "/tmp/registry.yaml",
        "surfaces": ["videos"],
        "regions": ["US"],
        "query_ids": [],
        "summary": {
            "videos": {
                "query_evaluations": 1,
                "passed_queries": 1,
                "raw_candidates": 5,
                "accepted_candidates": 3,
                "distinct_query_ids": 1,
                "top_queries": [],
            }
        },
        "results": [
            SearchKeyLabQueryResult(
                query_id="ai-video",
                category="AI",
                concept="OpenAI update",
                surface="videos",
                region="US",
                query="OpenAI update",
                order="date",
                priority=1,
                raw_candidates=5,
                accepted_candidates=3,
                distinct_channels=3,
                high_fit_candidates=3,
                filtered_non_english=0,
                filtered_live=0,
                filtered_off_topic=0,
                filtered_format=0,
                filtered_clickbait=0,
                filtered_duplicate=0,
                filtered_quality=0,
                non_english_rate=0.0,
                off_topic_rate=0.0,
                score=24,
                passed_thresholds=True,
                accepted_sample=[],
            ).__dict__
        ],
    }
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
queries:
  - id: ai-video
    category: AI
    concept: OpenAI update
    surfaces: [videos]
    mode: always_on
    youtube_fit: high
    priority: 1
    video_query: OpenAI update
    order: date
    enabled: true
""".strip(),
        encoding="utf-8",
    )

    paths = write_artifacts(
        report=report, output_dir=tmp_path / "artifacts", registry_path=registry_path
    )

    assert paths["results"].exists()
    assert paths["summary"].exists()
    assert paths["best_registry"].exists()
    payload = json.loads(paths["results"].read_text(encoding="utf-8"))
    assert payload["summary"]["videos"]["accepted_candidates"] == 3
    best_registry = paths["best_registry"].read_text(encoding="utf-8")
    assert "ai-video" in best_registry
    assert "OpenAI update" in render_summary_markdown(report)
