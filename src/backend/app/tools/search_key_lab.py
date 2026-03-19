"""Dry-run evaluator for YouTube search query registries.

This tool reuses the live YouTube search and discovery filtering logic without
writing ContentItem rows or VideoDiscoveryRun metrics. It is intended for
shadow optimization of candidate registries before any production rollout.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import yaml

from app.config.search_query_registry import (
    SearchQueryRegistryRow,
    _registry_path,
    clear_search_query_registry_cache,
    get_search_query_registry,
)
from app.config.video_discovery import discovery_cutoff
from app.integrations.youtube_client import VideoEntry, YouTubeClient
from app.services.video_discovery_service import VideoDiscoveryService

_DEFAULT_OUTPUT_ROOT = Path("/tmp/blips-search-lab")
_DEFAULT_SURFACES = ("videos", "reels")
_HIGH_FIT_THRESHOLD = 0.7
_VIDEO_ACCEPTED_MIN = 3
_REEL_ACCEPTED_MIN = 2
_MAX_ACCEPTED_SAMPLE = 5


@dataclass(frozen=True)
class SearchKeyLabQueryResult:
    """One dry-run evaluation for a single query / surface / region."""

    query_id: str
    category: str
    concept: str
    surface: str
    region: str
    query: str
    order: str
    priority: int
    raw_candidates: int
    accepted_candidates: int
    distinct_channels: int
    high_fit_candidates: int
    filtered_non_english: int
    filtered_live: int
    filtered_off_topic: int
    filtered_format: int
    filtered_clickbait: int
    filtered_duplicate: int
    filtered_quality: int
    non_english_rate: float
    off_topic_rate: float
    score: int
    passed_thresholds: bool
    accepted_sample: list[dict[str, Any]]


class _DryRunVideoSourceRepo:
    """No-op source repo used by the lab to avoid DB writes entirely."""

    def get_many(self, channel_ids: Iterable[str]) -> dict[str, Any]:
        del channel_ids
        return {}

    def upsert_discovered_channels(self, channels: Iterable[dict[str, str]]) -> list[Any]:
        del channels
        return []


@contextmanager
def _override_registry_path(registry_path: Path | None) -> Iterator[None]:
    original = os.getenv("SEARCH_QUERY_REGISTRY_PATH")
    if registry_path is not None:
        os.environ["SEARCH_QUERY_REGISTRY_PATH"] = str(registry_path)
    clear_search_query_registry_cache()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("SEARCH_QUERY_REGISTRY_PATH", None)
        else:
            os.environ["SEARCH_QUERY_REGISTRY_PATH"] = original
        clear_search_query_registry_cache()


def _parse_surfaces(surface: str) -> tuple[str, ...]:
    normalized = (surface or "both").strip().lower()
    if normalized == "both":
        return _DEFAULT_SURFACES
    if normalized in {"videos", "reels"}:
        return (normalized,)
    raise ValueError(f"Unsupported surface '{surface}'")


def _parse_regions(value: str | None) -> tuple[str, ...]:
    regions = tuple(
        dict.fromkeys(
            region.strip().upper() for region in (value or "US").split(",") if region.strip()
        )
    )
    if not regions:
        raise ValueError("At least one region is required")
    return regions


def _parse_query_ids(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip()))


def _load_registry_rows(
    *,
    registry_path: Path | None,
    surfaces: Sequence[str],
    query_ids: Sequence[str],
) -> tuple[SearchQueryRegistryRow, ...]:
    selected_surfaces = set(surfaces)
    selected_ids = set(query_ids)

    with _override_registry_path(registry_path):
        rows = tuple(
            row
            for row in get_search_query_registry()
            if row.enabled
            and row.mode == "always_on"
            and any(row.supports_surface(surface) for surface in selected_surfaces)
            and (not selected_ids or row.id in selected_ids)
        )
    return rows


def _accepted_sample(entry: VideoEntry) -> dict[str, Any]:
    return {
        "video_id": entry.video_id,
        "title": entry.title,
        "source": entry.source,
        "channel_id": entry.channel_id,
        "video_url": entry.video_url,
        "published_at": entry.published_at.isoformat() if entry.published_at else None,
        "duration_seconds": entry.duration_seconds,
        "view_count": entry.view_count,
        "format_fit_score": entry.format_fit_score,
        "query_label": entry.query_label,
    }


def _passed_thresholds(
    *,
    surface: str,
    raw_candidates: int,
    accepted_candidates: int,
    distinct_channels: int,
    filtered_non_english: int,
    filtered_off_topic: int,
) -> bool:
    if raw_candidates <= 0:
        return False
    accepted_min = _REEL_ACCEPTED_MIN if surface == "reels" else _VIDEO_ACCEPTED_MIN
    if accepted_candidates < accepted_min:
        return False
    if distinct_channels < 2:
        return False
    if filtered_non_english / raw_candidates > 0.30:
        return False
    off_topic_limit = 0.10 if surface == "reels" else 0.15
    if filtered_off_topic / raw_candidates > off_topic_limit:
        return False
    return True


def _score_query_result(
    *,
    accepted_candidates: int,
    distinct_channels: int,
    high_fit_candidates: int,
    filtered_non_english: int,
    filtered_off_topic: int,
    filtered_format: int,
) -> int:
    return (
        5 * accepted_candidates
        + 2 * distinct_channels
        + 3 * high_fit_candidates
        - 4 * filtered_non_english
        - 3 * filtered_off_topic
        - 2 * filtered_format
    )


def _build_service(youtube_client: YouTubeClient) -> VideoDiscoveryService:
    service = VideoDiscoveryService(db=None, youtube_client=youtube_client)
    service.repo = _DryRunVideoSourceRepo()
    return service


def evaluate_registry(
    *,
    registry_path: Path | None,
    surface: str = "both",
    regions: Sequence[str] = ("US",),
    query_ids: Sequence[str] = (),
    youtube_client: YouTubeClient | None = None,
) -> list[SearchKeyLabQueryResult]:
    """Evaluate a registry in dry-run mode and return per-query results."""

    surfaces = _parse_surfaces(surface)
    rows = _load_registry_rows(
        registry_path=registry_path,
        surfaces=surfaces,
        query_ids=query_ids,
    )
    client = youtube_client or YouTubeClient()
    service = _build_service(client)
    results: list[SearchKeyLabQueryResult] = []

    for current_surface in surfaces:
        cutoff = discovery_cutoff(current_surface)
        for row in rows:
            if not row.supports_surface(current_surface):
                continue
            query = row.query_for(current_surface)
            if not query:
                continue
            for region in regions:
                candidates = client.fetch_search_candidates(
                    query,
                    region_code=region,
                    max_results=25,
                    surface=current_surface,
                    search_order=row.order,
                    query_label=row.id,
                    published_after=cutoff,
                )
                accepted, counters = service._filter_candidates(candidates, current_surface)
                high_fit = sum(
                    1
                    for entry in accepted
                    if float(entry.format_fit_score or 0.0) >= _HIGH_FIT_THRESHOLD
                )
                distinct_channels = len(
                    {
                        (entry.channel_id or "").strip() or (entry.source or "").strip().lower()
                        for entry in accepted
                        if (entry.channel_id or entry.source)
                    }
                )
                raw_candidates = len(candidates)
                filtered_non_english = int(counters.get("non_english", 0))
                filtered_off_topic = int(counters.get("off_topic", 0))
                filtered_format = int(counters.get("format", 0))
                result = SearchKeyLabQueryResult(
                    query_id=row.id,
                    category=row.category,
                    concept=row.concept,
                    surface=current_surface,
                    region=region,
                    query=query,
                    order=row.order,
                    priority=row.priority,
                    raw_candidates=raw_candidates,
                    accepted_candidates=len(accepted),
                    distinct_channels=distinct_channels,
                    high_fit_candidates=high_fit,
                    filtered_non_english=filtered_non_english,
                    filtered_live=int(counters.get("live", 0)),
                    filtered_off_topic=filtered_off_topic,
                    filtered_format=filtered_format,
                    filtered_clickbait=int(counters.get("clickbait", 0)),
                    filtered_duplicate=int(counters.get("duplicate", 0)),
                    filtered_quality=int(counters.get("quality", 0)),
                    non_english_rate=(
                        round(filtered_non_english / raw_candidates, 4) if raw_candidates else 0.0
                    ),
                    off_topic_rate=(
                        round(filtered_off_topic / raw_candidates, 4) if raw_candidates else 0.0
                    ),
                    score=_score_query_result(
                        accepted_candidates=len(accepted),
                        distinct_channels=distinct_channels,
                        high_fit_candidates=high_fit,
                        filtered_non_english=filtered_non_english,
                        filtered_off_topic=filtered_off_topic,
                        filtered_format=filtered_format,
                    ),
                    passed_thresholds=_passed_thresholds(
                        surface=current_surface,
                        raw_candidates=raw_candidates,
                        accepted_candidates=len(accepted),
                        distinct_channels=distinct_channels,
                        filtered_non_english=filtered_non_english,
                        filtered_off_topic=filtered_off_topic,
                    ),
                    accepted_sample=[
                        _accepted_sample(entry) for entry in accepted[:_MAX_ACCEPTED_SAMPLE]
                    ],
                )
                results.append(result)

    return sorted(results, key=lambda item: (item.surface, item.query_id, item.region))


def _surface_summary(results: Sequence[SearchKeyLabQueryResult]) -> dict[str, Any]:
    if not results:
        return {
            "query_evaluations": 0,
            "passed_queries": 0,
            "raw_candidates": 0,
            "accepted_candidates": 0,
            "distinct_query_ids": 0,
            "top_queries": [],
        }
    top_queries = sorted(
        results, key=lambda item: (item.score, item.accepted_candidates), reverse=True
    )[:5]
    return {
        "query_evaluations": len(results),
        "passed_queries": sum(1 for item in results if item.passed_thresholds),
        "raw_candidates": sum(item.raw_candidates for item in results),
        "accepted_candidates": sum(item.accepted_candidates for item in results),
        "distinct_query_ids": len({item.query_id for item in results}),
        "top_queries": [
            {
                "query_id": item.query_id,
                "region": item.region,
                "score": item.score,
                "accepted_candidates": item.accepted_candidates,
                "distinct_channels": item.distinct_channels,
            }
            for item in top_queries
        ],
    }


def build_report(
    *,
    registry_path: Path | None,
    surface: str,
    regions: Sequence[str],
    query_ids: Sequence[str],
    youtube_client: YouTubeClient | None = None,
) -> dict[str, Any]:
    surfaces = _parse_surfaces(surface)
    client = youtube_client or YouTubeClient()
    if hasattr(client, "_youtube_api_key") and callable(client._youtube_api_key):  # type: ignore[attr-defined]
        api_key = client._youtube_api_key()  # type: ignore[attr-defined]
        if not api_key:
            raise RuntimeError(
                "YOUTUBE_API_KEY is required for search-key lab runs against the YouTube API"
            )
    results = evaluate_registry(
        registry_path=registry_path,
        surface=surface,
        regions=regions,
        query_ids=query_ids,
        youtube_client=client,
    )
    summary = {
        current_surface: _surface_summary([r for r in results if r.surface == current_surface])
        for current_surface in surfaces
    }
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "registry_path": str((registry_path or _registry_path()).resolve()),
        "surfaces": list(surfaces),
        "regions": list(regions),
        "query_ids": list(query_ids),
        "summary": summary,
        "results": [asdict(result) for result in results],
    }


def render_summary_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Search Key Lab Summary",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Registry: `{report.get('registry_path')}`",
        f"- Regions: `{', '.join(report.get('regions') or [])}`",
        "",
    ]
    summary = report.get("summary") or {}
    results = report.get("results") or []
    for surface in report.get("surfaces") or []:
        surface_summary = summary.get(surface) or {}
        lines.extend(
            [
                f"## {surface.title()}",
                "",
                f"- Query evaluations: `{surface_summary.get('query_evaluations', 0)}`",
                f"- Passed queries: `{surface_summary.get('passed_queries', 0)}`",
                f"- Raw candidates: `{surface_summary.get('raw_candidates', 0)}`",
                f"- Accepted candidates: `{surface_summary.get('accepted_candidates', 0)}`",
                "",
                "| Query | Query text | Region | Raw | Accepted | Distinct channels | Score | Passed |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        surface_results = [item for item in results if item.get("surface") == surface]
        if surface_results:
            for item in sorted(
                surface_results,
                key=lambda row: (row.get("score", 0), row.get("accepted_candidates", 0)),
                reverse=True,
            ):
                lines.append(
                    "| {query} | {query_text} | {region} | {raw} | {accepted} | {channels} | {score} | {passed} |".format(
                        query=item.get("query_id"),
                        query_text=item.get("query"),
                        region=item.get("region"),
                        raw=item.get("raw_candidates"),
                        accepted=item.get("accepted_candidates"),
                        channels=item.get("distinct_channels"),
                        score=item.get("score"),
                        passed="yes" if item.get("passed_thresholds") else "no",
                    )
                )
        else:
            lines.append("| — | — | — | 0 | 0 | 0 | 0 | no |")
        lines.append("")
    return "\n".join(lines)


def _best_registry_rows(
    report: dict[str, Any],
    *,
    registry_path: Path | None,
) -> list[dict[str, Any]]:
    results = report.get("results") or []
    result_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in results:
        key = (str(item.get("query_id") or ""), str(item.get("surface") or ""))
        result_map.setdefault(key, []).append(item)

    rows = _load_registry_rows(
        registry_path=registry_path,
        surfaces=tuple(report.get("surfaces") or _DEFAULT_SURFACES),
        query_ids=tuple(report.get("query_ids") or ()),
    )
    best_rows: list[dict[str, Any]] = []
    for row in rows:
        supported_surfaces = [
            surface for surface in report.get("surfaces") or [] if row.supports_surface(surface)
        ]
        if not supported_surfaces:
            continue
        if all(
            any(result.get("passed_thresholds") for result in result_map.get((row.id, surface), []))
            for surface in supported_surfaces
        ):
            best_rows.append(
                {
                    "id": row.id,
                    "category": row.category,
                    "concept": row.concept,
                    "surfaces": list(row.surfaces),
                    "mode": row.mode,
                    "youtube_fit": row.youtube_fit,
                    "priority": row.priority,
                    "video_query": row.video_query,
                    "reel_query": row.reel_query,
                    "order": row.order,
                    "enabled": row.enabled,
                }
            )
    return best_rows


def write_artifacts(
    *,
    report: dict[str, Any],
    output_dir: Path,
    registry_path: Path | None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.json"
    summary_path = output_dir / "summary.md"
    best_registry_path = output_dir / "best_registry.yaml"
    results_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    summary_path.write_text(render_summary_markdown(report), encoding="utf-8")
    best_registry = {"queries": _best_registry_rows(report, registry_path=registry_path)}
    best_registry_path.write_text(
        yaml.safe_dump(best_registry, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    return {
        "results": results_path,
        "summary": summary_path,
        "best_registry": best_registry_path,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate a YouTube search query registry in dry-run mode.",
    )
    parser.add_argument(
        "--registry",
        default=None,
        help="Optional YAML registry path. Defaults to the current runtime registry.",
    )
    parser.add_argument(
        "--surface",
        default="both",
        choices=["videos", "reels", "both"],
        help="Surface to evaluate.",
    )
    parser.add_argument(
        "--regions",
        default="US",
        help="Comma-separated region codes. Default: US",
    )
    parser.add_argument(
        "--query-ids",
        default="",
        help="Optional comma-separated query ids to restrict evaluation.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for artifacts. Defaults to /tmp/blips-search-lab/<timestamp>.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    registry_path = Path(args.registry).expanduser().resolve() if args.registry else None
    regions = _parse_regions(args.regions)
    query_ids = _parse_query_ids(args.query_ids)
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else _DEFAULT_OUTPUT_ROOT / datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    )
    report = build_report(
        registry_path=registry_path,
        surface=args.surface,
        regions=regions,
        query_ids=query_ids,
    )
    artifact_paths = write_artifacts(
        report=report,
        output_dir=output_dir,
        registry_path=registry_path,
    )
    print(json.dumps({name: str(path) for name, path in artifact_paths.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
