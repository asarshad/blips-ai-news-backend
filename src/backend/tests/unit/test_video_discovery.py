from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta

import pytest

from app import main as main_module
from app.config import video_discovery as video_discovery_module
from app.config.search_query_registry import clear_search_query_registry_cache
from app.config.video_discovery import discovery_cutoff, get_query_packs


def test_discovery_queries_no_longer_use_today_keyword():
    video_queries = [pack.query for pack in get_query_packs("videos")]
    reel_queries = [pack.query for pack in get_query_packs("reels")]

    assert all("today" not in query.lower() for query in video_queries)
    assert all("today" not in query.lower() for query in reel_queries)


def test_discovery_cutoff_uses_weekly_window():
    cutoff = discovery_cutoff("videos")
    now = datetime.utcnow()

    assert now - timedelta(days=8) < cutoff < now - timedelta(days=6)


def test_query_packs_use_large_result_pages():
    assert all(pack.max_results == 25 for pack in get_query_packs("videos"))
    assert all(pack.max_results == 25 for pack in get_query_packs("reels"))


def test_query_packs_use_hotness_aware_search_orders():
    assert any(pack.order == "date" for pack in get_query_packs("videos"))
    assert any(pack.order == "viewCount" for pack in get_query_packs("reels"))
    assert all(
        pack.order in {"date", "relevance", "viewCount"} for pack in get_query_packs("videos")
    )
    assert all(
        pack.order in {"date", "relevance", "viewCount"} for pack in get_query_packs("reels")
    )


def test_query_packs_expose_registry_ids_as_labels():
    video_labels = {pack.label for pack in get_query_packs("videos")}
    reel_labels = {pack.label for pack in get_query_packs("reels")}

    assert "ai-models" in video_labels
    assert "launch-highlights" in video_labels
    assert "ai-shorts" in reel_labels
    assert "keynote-shorts" in reel_labels


def test_video_discovery_module_validates_registry_at_import(monkeypatch, tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(
        """
queries:
  - id: broken-row
    category: AI
    concept: Broken
    surfaces: [videos]
    mode: always_on
    youtube_fit: high
    priority: 1
    order: date
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SEARCH_QUERY_REGISTRY_PATH", str(path))
    clear_search_query_registry_cache()

    with pytest.raises(ValueError, match="missing video_query"):
        importlib.reload(video_discovery_module)

    monkeypatch.delenv("SEARCH_QUERY_REGISTRY_PATH", raising=False)
    clear_search_query_registry_cache()
    importlib.reload(video_discovery_module)


def test_app_lifespan_validates_registry_on_startup(monkeypatch, tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(
        """
queries:
  - id: broken-row
    category: AI
    concept: Broken
    surfaces: [videos]
    mode: always_on
    youtube_fit: high
    priority: 1
    order: date
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SEARCH_QUERY_REGISTRY_PATH", str(path))
    monkeypatch.setenv("SKIP_STARTUP_CHECKS", "true")
    clear_search_query_registry_cache()

    async def _run():
        with pytest.raises(ValueError, match="missing video_query"):
            async with main_module.lifespan(object()):
                pass

    asyncio.run(_run())

    monkeypatch.delenv("SEARCH_QUERY_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("SKIP_STARTUP_CHECKS", raising=False)
    clear_search_query_registry_cache()
