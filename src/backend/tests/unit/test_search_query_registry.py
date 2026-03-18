from __future__ import annotations

import textwrap

import pytest

from app.config.search_query_registry import (
    clear_search_query_registry_cache,
    iter_runtime_registry_rows,
    load_search_query_registry,
)


def test_load_search_query_registry_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(
        textwrap.dedent(
            """
            queries:
              - id: duplicate
                category: AI
                concept: First
                surfaces: [videos]
                mode: always_on
                youtube_fit: high
                priority: 1
                video_query: OpenAI update
                order: date
                enabled: true
              - id: duplicate
                category: AI
                concept: Second
                surfaces: [videos]
                mode: always_on
                youtube_fit: high
                priority: 2
                video_query: Gemini update
                order: date
                enabled: true
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate search query registry id"):
        load_search_query_registry(path)


def test_load_search_query_registry_requires_surface_queries(tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(
        textwrap.dedent(
            """
            queries:
              - id: missing-video-query
                category: AI
                concept: Broken row
                surfaces: [videos]
                mode: always_on
                youtube_fit: high
                priority: 1
                order: date
                enabled: true
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing video_query"):
        load_search_query_registry(path)


def test_load_search_query_registry_rejects_duplicate_always_on_queries_per_surface(tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(
        textwrap.dedent(
            """
            queries:
              - id: openai-video
                category: AI
                concept: OpenAI updates
                surfaces: [videos]
                mode: always_on
                youtube_fit: high
                priority: 1
                video_query: OpenAI Gemini Claude update
                order: date
                enabled: true
              - id: duplicate-query
                category: AI
                concept: Frontier models
                surfaces: [videos]
                mode: always_on
                youtube_fit: high
                priority: 2
                video_query:  OpenAI   Gemini Claude update
                order: date
                enabled: true
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate always_on query"):
        load_search_query_registry(path)


def test_load_search_query_registry_rejects_duplicate_always_on_concepts_per_surface(tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(
        textwrap.dedent(
            """
            queries:
              - id: openai-video
                category: AI
                concept: Frontier AI updates
                surfaces: [videos]
                mode: always_on
                youtube_fit: high
                priority: 1
                video_query: OpenAI Gemini Claude update
                order: date
                enabled: true
              - id: gemini-video
                category: AI
                concept: frontier ai updates
                surfaces: [videos]
                mode: always_on
                youtube_fit: high
                priority: 2
                video_query: Gemini launch update
                order: date
                enabled: true
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate always_on concept"):
        load_search_query_registry(path)


def test_iter_runtime_registry_rows_ignores_non_always_on_modes(monkeypatch, tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(
        textwrap.dedent(
            """
            queries:
              - id: active-video
                category: AI
                concept: Active row
                surfaces: [videos]
                mode: always_on
                youtube_fit: high
                priority: 1
                video_query: OpenAI Gemini Claude update
                order: date
                enabled: true
              - id: story-video
                category: Cloud
                concept: Story only
                surfaces: [videos]
                mode: story_only
                youtube_fit: medium
                priority: 2
                video_query: Kubernetes release overview
                order: relevance
                enabled: true
              - id: parked-reel
                category: Consumer Tech
                concept: Parked
                surfaces: [reels]
                mode: parked
                youtube_fit: low
                priority: 4
                reel_query: gadget shorts
                order: viewCount
                enabled: false
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SEARCH_QUERY_REGISTRY_PATH", str(path))
    clear_search_query_registry_cache()

    rows = iter_runtime_registry_rows("videos")

    assert [row.id for row in rows] == ["active-video"]

    monkeypatch.delenv("SEARCH_QUERY_REGISTRY_PATH", raising=False)
    clear_search_query_registry_cache()
