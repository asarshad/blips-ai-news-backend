"""TLDR-derived source registry (v1).

This module builds a curated shortlist from a raw TLDR source crawl so the
pipeline can use a manageable domain set instead of the full long tail.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

SOURCE_REGISTRY_VERSION = "tldr-shortlist-v1-2026-03-06"

# Snapshot generated from the 2026-03-06 TLDR crawl domain-count export
# (1,351 unique domains). The checked-in CSV below is the runtime input.
_DOMAIN_COUNTS_PATH = Path(__file__).resolve().parent / "data" / "tldr_domain_counts_2026_03_06.csv"

DEFAULT_MIN_MENTIONS = 6
DEFAULT_MAX_DOMAINS = 120

# Exclusions for v1 shortlist generation.
# - social/mirror domains are noisy for direct ingestion
# - sponsor destinations should not become editorial sources
# - generic platforms are often not canonical publishers in this dataset
EXCLUDED_DOMAINS: FrozenSet[str] = frozenset(
    {
        "advertise.tldr.tech",
        "github.com",
        "linkedin.com",
        "medium.com",
        "threadreaderapp.com",
        "x.com",
        "youtube.com",
    }
)

# Minimal handling for common multi-label public suffixes.
_MULTI_LABEL_SUFFIXES: FrozenSet[str] = frozenset(
    {
        "co.uk",
        "org.uk",
        "gov.uk",
        "ac.uk",
        "co.jp",
        "com.au",
        "com.br",
    }
)


@dataclass(frozen=True)
class SourceRegistryEntry:
    """Single shortlisted source domain entry."""

    rank: int
    domain: str
    mentions: int
    registry: str = SOURCE_REGISTRY_VERSION


def normalize_domain(domain_or_url: str) -> str:
    """Normalize a domain/URL into lowercase hostname form."""
    if not domain_or_url:
        return ""

    value = domain_or_url.strip().lower()
    parsed = urlparse(value)

    # urlparse("example.com") puts it in `path`, so retry with scheme.
    host = parsed.netloc or ""
    if not host:
        parsed = urlparse(f"https://{value}")
        host = parsed.netloc or parsed.path

    host = host.split("@")[-1].split(":")[0].strip(".")
    while host.startswith("www."):
        host = host[4:]

    return host


def registered_domain(host: str) -> str:
    """Return an approximate registrable domain from a hostname."""
    normalized = normalize_domain(host)
    if not normalized:
        return ""

    labels = normalized.split(".")
    if len(labels) <= 2:
        return normalized

    suffix_two = ".".join(labels[-2:])
    if suffix_two in _MULTI_LABEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])

    return ".".join(labels[-2:])


def _read_domain_counts(path: Path) -> List[Tuple[str, int]]:
    rows: List[Tuple[str, int]] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for raw_domain, raw_count in reader:
            domain = normalize_domain(raw_domain)
            if not domain:
                continue
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            rows.append((domain, count))
    return rows


def build_tldr_shortlist(
    *,
    min_mentions: int = DEFAULT_MIN_MENTIONS,
    max_domains: int = DEFAULT_MAX_DOMAINS,
    excluded_domains: Iterable[str] = EXCLUDED_DOMAINS,
) -> List[SourceRegistryEntry]:
    """Build shortlist from TLDR crawl domain-frequency data."""
    excluded = {normalize_domain(d) for d in excluded_domains}
    counts = _read_domain_counts(_DOMAIN_COUNTS_PATH)

    shortlist: List[SourceRegistryEntry] = []
    seen: set[str] = set()

    for domain, mentions in counts:
        if mentions < min_mentions:
            continue
        if domain in excluded:
            continue
        if domain in seen:
            continue

        shortlist.append(
            SourceRegistryEntry(
                rank=len(shortlist) + 1,
                domain=domain,
                mentions=mentions,
            )
        )
        seen.add(domain)

        if len(shortlist) >= max_domains:
            break

    return shortlist


@lru_cache(maxsize=1)
def get_tldr_source_shortlist() -> Tuple[SourceRegistryEntry, ...]:
    """Cached TLDR shortlist registry (v1)."""
    return tuple(build_tldr_shortlist())


@lru_cache(maxsize=1)
def get_tldr_source_index() -> Dict[str, SourceRegistryEntry]:
    """Domain -> source entry lookup for the v1 shortlist."""
    return {entry.domain: entry for entry in get_tldr_source_shortlist()}


def get_source_registry_entry(domain_or_url: str) -> Optional[SourceRegistryEntry]:
    """Resolve a domain/URL to a shortlist entry if present."""
    index = get_tldr_source_index()
    normalized = normalize_domain(domain_or_url)
    if not normalized:
        return None

    if normalized in index:
        return index[normalized]

    root = registered_domain(normalized)
    return index.get(root)


def is_shortlisted_source(domain_or_url: str) -> bool:
    """Return True if domain/URL resolves to a shortlist source."""
    return get_source_registry_entry(domain_or_url) is not None


def get_source_registry_stats() -> Dict[str, int]:
    """Return compact stats for observability/debugging."""
    shortlist = get_tldr_source_shortlist()
    return {
        "snapshot_domains": len(_read_domain_counts(_DOMAIN_COUNTS_PATH)),
        "shortlist_domains": len(shortlist),
        "min_mentions": DEFAULT_MIN_MENTIONS,
        "excluded_domains": len(EXCLUDED_DOMAINS),
    }


__all__ = [
    "SOURCE_REGISTRY_VERSION",
    "DEFAULT_MIN_MENTIONS",
    "DEFAULT_MAX_DOMAINS",
    "EXCLUDED_DOMAINS",
    "SourceRegistryEntry",
    "normalize_domain",
    "registered_domain",
    "build_tldr_shortlist",
    "get_tldr_source_shortlist",
    "get_tldr_source_index",
    "get_source_registry_entry",
    "is_shortlisted_source",
    "get_source_registry_stats",
]
