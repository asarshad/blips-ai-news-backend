"""Load and validate the static YouTube discovery query registry."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Sequence

import yaml

RegistryMode = Literal["always_on", "story_only", "parked"]
RegistrySurface = Literal["videos", "reels"]
RegistryYoutubeFit = Literal["high", "medium", "low"]
RegistryOrder = Literal["date", "relevance", "viewCount"]

_VALID_MODES = {"always_on", "story_only", "parked"}
_VALID_SURFACES = {"videos", "reels"}
_VALID_YOUTUBE_FIT = {"high", "medium", "low"}
_VALID_ORDERS = {"date", "relevance", "viewCount"}
_VALID_PRIORITIES = {1, 2, 3, 4}
_ID_PATTERN = re.compile(r"^[a-z0-9-]{1,32}$")
_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class SearchQueryRegistryRow:
    """One validated editorial query row from the registry."""

    id: str
    category: str
    concept: str
    surfaces: tuple[RegistrySurface, ...]
    mode: RegistryMode
    youtube_fit: RegistryYoutubeFit
    priority: int
    video_query: str | None
    reel_query: str | None
    order: RegistryOrder
    enabled: bool

    def query_for(self, surface: str) -> str | None:
        return self.reel_query if surface == "reels" else self.video_query

    def supports_surface(self, surface: str) -> bool:
        return surface in self.surfaces


def _registry_path() -> Path:
    override = os.getenv("SEARCH_QUERY_REGISTRY_PATH")
    if override:
        return Path(override)
    return Path(__file__).with_name("search_query_registry.yaml")


def _require_text(raw: Any, *, field: str, row_id: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError(f"Registry row '{row_id}' is missing required field '{field}'")
    return value


def _coerce_surfaces(value: Any, *, row_id: str) -> tuple[RegistrySurface, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Registry row '{row_id}' must declare at least one surface")
    surfaces = tuple(str(item).strip().lower() for item in value if str(item).strip())
    if not surfaces:
        raise ValueError(f"Registry row '{row_id}' must declare at least one surface")
    invalid = [surface for surface in surfaces if surface not in _VALID_SURFACES]
    if invalid:
        raise ValueError(
            f"Registry row '{row_id}' has invalid surfaces: {', '.join(sorted(set(invalid)))}"
        )
    return tuple(dict.fromkeys(surfaces))


def _validate_row(raw: dict[str, Any]) -> SearchQueryRegistryRow:
    row_id = _require_text(raw.get("id"), field="id", row_id="<unknown>").lower()
    if not _ID_PATTERN.fullmatch(row_id):
        raise ValueError(f"Registry row '{row_id}' must match pattern '{_ID_PATTERN.pattern}'")

    mode = str(raw.get("mode") or "").strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(f"Registry row '{row_id}' has invalid mode '{mode}'")

    youtube_fit = str(raw.get("youtube_fit") or "").strip().lower()
    if youtube_fit not in _VALID_YOUTUBE_FIT:
        raise ValueError(f"Registry row '{row_id}' has invalid youtube_fit '{youtube_fit}'")

    try:
        priority = int(raw.get("priority"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Registry row '{row_id}' must define an integer priority") from exc
    if priority not in _VALID_PRIORITIES:
        raise ValueError(f"Registry row '{row_id}' has invalid priority '{priority}'")

    order = str(raw.get("order") or "").strip()
    if order not in _VALID_ORDERS:
        raise ValueError(f"Registry row '{row_id}' has invalid order '{order}'")

    surfaces = _coerce_surfaces(raw.get("surfaces"), row_id=row_id)
    video_query = str(raw.get("video_query") or "").strip() or None
    reel_query = str(raw.get("reel_query") or "").strip() or None

    if "videos" in surfaces and not video_query:
        raise ValueError(f"Registry row '{row_id}' is missing video_query for videos")
    if "reels" in surfaces and not reel_query:
        raise ValueError(f"Registry row '{row_id}' is missing reel_query for reels")

    return SearchQueryRegistryRow(
        id=row_id,
        category=_require_text(raw.get("category"), field="category", row_id=row_id),
        concept=_require_text(raw.get("concept"), field="concept", row_id=row_id),
        surfaces=surfaces,
        mode=mode,  # type: ignore[arg-type]
        youtube_fit=youtube_fit,  # type: ignore[arg-type]
        priority=priority,
        video_query=video_query,
        reel_query=reel_query,
        order=order,  # type: ignore[arg-type]
        enabled=bool(raw.get("enabled", True)),
    )


def _normalize_text(value: str | None) -> str:
    return _WHITESPACE_PATTERN.sub(" ", str(value or "").strip().lower())


def load_search_query_registry(path: Path | None = None) -> tuple[SearchQueryRegistryRow, ...]:
    """Load the query registry from YAML and validate runtime constraints."""

    registry_path = path or _registry_path()
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    raw_rows = payload.get("queries") if isinstance(payload, dict) else payload

    if not isinstance(raw_rows, list):
        raise ValueError("Search query registry must define a top-level 'queries' list")

    rows = tuple(_validate_row(raw) for raw in raw_rows if isinstance(raw, dict))
    if len(rows) != len(raw_rows):
        raise ValueError("Search query registry rows must be mapping objects")

    seen_ids: set[str] = set()
    seen_queries: set[tuple[RegistrySurface, str]] = set()
    seen_concepts: set[tuple[RegistrySurface, str]] = set()
    for row in rows:
        if row.id in seen_ids:
            raise ValueError(f"Duplicate search query registry id '{row.id}'")
        seen_ids.add(row.id)
        if not row.enabled or row.mode != "always_on":
            continue
        for surface in row.surfaces:
            concept_key = (surface, _normalize_text(row.concept))
            if concept_key in seen_concepts:
                raise ValueError(
                    f"Duplicate always_on concept '{row.concept}' for surface '{surface}'"
                )
            seen_concepts.add(concept_key)

            query = row.query_for(surface)
            normalized_query = _normalize_text(query)
            if normalized_query:
                query_key = (surface, normalized_query)
                if query_key in seen_queries:
                    raise ValueError(f"Duplicate always_on query '{query}' for surface '{surface}'")
                seen_queries.add(query_key)

    return rows


@lru_cache(maxsize=1)
def get_search_query_registry() -> tuple[SearchQueryRegistryRow, ...]:
    """Return the cached runtime registry."""

    return load_search_query_registry()


def clear_search_query_registry_cache() -> None:
    """Test helper for cache invalidation."""

    get_search_query_registry.cache_clear()


def iter_runtime_registry_rows(
    surface: str,
    *,
    mode: RegistryMode = "always_on",
    enabled_only: bool = True,
) -> Sequence[SearchQueryRegistryRow]:
    """Return rows eligible for runtime use on a given surface."""

    filtered: list[SearchQueryRegistryRow] = []
    for row in get_search_query_registry():
        if enabled_only and not row.enabled:
            continue
        if row.mode != mode:
            continue
        if not row.supports_surface(surface):
            continue
        filtered.append(row)
    return tuple(filtered)
