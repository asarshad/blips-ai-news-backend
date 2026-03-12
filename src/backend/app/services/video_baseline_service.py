"""Helpers for loading committed video/reel baseline snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_VIDEO_BASELINE_TAG = "pre-video-reels-overhaul-2026-03-12"


def baseline_dir() -> Path:
    """Return the committed baseline artifact directory."""
    return Path(__file__).resolve().parents[1] / "data" / "video_baselines"


def default_baseline_tag() -> str:
    """Return the default tag used in admin comparisons."""
    return DEFAULT_VIDEO_BASELINE_TAG


def load_baseline_snapshot(tag: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load a committed baseline snapshot by tag name."""
    selected = tag or DEFAULT_VIDEO_BASELINE_TAG
    path = baseline_dir() / f"{selected}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def numeric_delta(current: Any, previous: Any) -> Optional[float]:
    """Return a rounded delta when both values are numeric."""
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
        return None
    return round(float(current) - float(previous), 2)
