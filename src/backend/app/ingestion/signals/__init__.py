"""Signal source fetchers for the Coverage Guarantee pipeline.

Each sub-module exposes a single ``fetch()`` function returning a list of
``SignalItem`` dataclasses.  The signal orchestrator calls each fetcher,
canonicalizes URLs, and cross-checks against ``content_items``.

Fetchers are PURE – they have no DB access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.models.signal import SignalSource


@dataclass
class SignalItem:
    """A single URL discovered by a trend-signal source."""

    raw_url: str
    signal_source: SignalSource
    raw_title: Optional[str] = None
    # Source-specific popularity proxy (HN score, GH star rank, YT view count)
    signal_score: Optional[int] = None
