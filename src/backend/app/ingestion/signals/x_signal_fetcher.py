"""X (Twitter) signal fetcher — signal amplification only.

This fetcher queries the Twitter v2 API to detect URLs being shared by
trusted tech accounts or matching topical queries. It extracts and returns
article/video URLs as ``SignalItem`` objects for injection into the existing
signal pipeline.

IMPORTANT DESIGN CONTRACT
--------------------------
- X posts are NEVER ingested as feed content.
- Only the URLs embedded in tweets are extracted.
- The existing signal orchestrator decides (via its normal path) whether a
  resolved URL becomes an ARTICLE, VIDEO, or gets suppressed.
- This module has no DB access. It is a pure fetcher.

Modes
-----
cohort  – query from a curated list of trusted tech accounts
query   – topical keyword search across all public tweets
mixed   – cohort + query combined (deduplicated)

Disabling
---------
Return ``[]`` immediately if:
  - ``bearer_token`` is empty
  - ``mode == "off"``
  - Feature flag ``x_signals`` is False (checked at task level, not here)
"""

from __future__ import annotations

import ipaddress
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Set
from urllib.parse import urlsplit

import requests

from app.ingestion.signals import SignalItem
from app.ingestion.url_normalizer import normalize_url
from app.models.signal import SignalSource

logger = logging.getLogger(__name__)

# ── Twitter API v2 ────────────────────────────────────────────────────────────

_X_API_BASE = "https://api.twitter.com/2"

# Twitter API v2 caps max_results at 100 for recent search.
_TWITTER_MAX_RESULTS_CAP = 100

# ── Default curated cohort ────────────────────────────────────────────────────
# Trusted tech accounts whose shared links are high-signal.
# Usernames without '@'. Users can override via X_SIGNALS_COHORT_ACCOUNTS.
_DEFAULT_COHORT_ACCOUNTS: Sequence[str] = (
    "OpenAI",
    "AnthropicAI",
    "GoogleDeepMind",
    "nvidia",
    "sama",
    "karpathy",
    "ylecun",
    "demishassabis",
    "ycombinator",
    "TechCrunch",
    "verge",
    "wired",
    "arstechnica",
    "benedictevans",
    "stratechery",
)

# ── Default topical queries ───────────────────────────────────────────────────
# Used in query/mixed mode. The base filter suffix is appended automatically.
_DEFAULT_QUERY_TERMS: Sequence[str] = (
    "AI model release",
    "LLM benchmark",
    "chip announcement",
    "security breach",
    "open source release",
)

_QUERY_BASE_FILTERS = "has:links lang:en -is:retweet"

# Max accounts per cohort sub-query (Twitter query length limit: 512 chars).
_MAX_ACCOUNTS_PER_QUERY = 15

# ── URL rejection ─────────────────────────────────────────────────────────────

# Domains whose content should never be passed to the signal pipeline.
# These are Twitter/media-hosting domains that appear in tweet entities.
_REJECT_DOMAINS: frozenset[str] = frozenset(
    {
        "twitter.com",
        "x.com",
        "t.co",
        "pic.twitter.com",
        "pbs.twimg.com",
        "abs.twimg.com",
        "twimg.com",
        "cards.twitter.com",
    }
)

# Path fragments that indicate a non-content page.
_NON_CONTENT_PATH_FRAGMENTS: tuple[str, ...] = (
    "/channel/",
    "/@",
    "/c/",
    "/user/",
    "/tag/",
    "/topic/",
    "/category/",
    "/search",
    "/playlist",
    "/feed",
    "/archive",
)

# Private/reserved address ranges for SSRF protection.
_PRIVATE_NETWORKS: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
)

_PRIVATE_HOSTNAMES: frozenset[str] = frozenset(
    {"localhost", "localtest.me", "metadata.google.internal", "169.254.169.254"}
)


# ── Metrics ───────────────────────────────────────────────────────────────────


@dataclass
class XSignalMetrics:
    """Per-run counters for structured logging and observability."""

    posts_seen: int = 0
    urls_extracted: int = 0
    urls_resolved: int = 0
    urls_rejected: int = 0
    items_returned: int = 0
    api_calls: int = 0
    errors: List[str] = field(default_factory=list)


# ── URL safety validation ─────────────────────────────────────────────────────


def _is_private_host(hostname: str) -> bool:
    """True if hostname is a private/reserved IP address or well-known internal name."""
    if hostname in _PRIVATE_HOSTNAMES:
        return True
    if hostname.endswith(".local") or hostname.endswith(".internal"):
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def _is_safe_content_url(url: str, *, debug: bool = False) -> bool:
    """Return True if the URL is safe and points to actual content.

    Rejects:
    - Non-http(s) schemes
    - Twitter/media-hosting self-links
    - Private/internal IP hosts (SSRF protection)
    - Homepage-only URLs (empty or '/' path)
    - Known non-content path patterns (channel pages, profiles, etc.)
    """
    if not url:
        return False

    try:
        parts = urlsplit(url)
    except Exception:
        return False

    if parts.scheme not in ("http", "https"):
        if debug:
            logger.debug("[x_signal] reject url=%s reason=scheme", url[:100])
        return False

    hostname = (parts.hostname or "").lower()
    if not hostname:
        return False

    # Reject known social/media-only domains and their subdomains.
    if hostname in _REJECT_DOMAINS or any(
        hostname.endswith(f".{d}") for d in _REJECT_DOMAINS
    ):
        if debug:
            logger.debug("[x_signal] reject url=%s reason=reject_domain host=%s", url[:100], hostname)
        return False

    # SSRF: reject private/internal IP addresses.
    if _is_private_host(hostname):
        if debug:
            logger.debug("[x_signal] reject url=%s reason=private_host host=%s", url[:100], hostname)
        return False

    path = parts.path or "/"

    # Reject homepage-only URLs — no article/video path.
    if path in ("", "/"):
        if debug:
            logger.debug("[x_signal] reject url=%s reason=homepage", url[:100])
        return False

    # Reject channel/profile/section pages.
    for fragment in _NON_CONTENT_PATH_FRAGMENTS:
        if fragment in path:
            if debug:
                logger.debug(
                    "[x_signal] reject url=%s reason=non_content_path fragment=%s",
                    url[:100],
                    fragment,
                )
            return False

    return True


# ── Scoring ───────────────────────────────────────────────────────────────────


def compute_x_signal_score(
    *,
    in_cohort: bool,
    like_count: int,
    retweet_count: int,
    age_hours: float,
) -> int:
    """Compute a conservative quality score [1, 100] for a signal URL.

    Design principles:
    - Cohort account tier dominates; raw engagement is a small bonus.
    - Time decay is aggressive — stale tweets should not surface old content.
    - Raw virality alone cannot promote a URL (promotion_service decides that).

    Formula: tier_weight + engagement_bonus - time_decay + base
    """
    # Cohort (trusted) accounts get significantly higher weight.
    tier_weight = 40 if in_cohort else 15

    # Engagement: log-scaled to prevent viral gaming. Retweets weighted 2x.
    weighted_engagement = max(1, like_count + retweet_count * 2)
    engagement_bonus = min(20, int(math.log2(weighted_engagement) * 2))

    # Time decay: 2 points per hour, capped at 30. 15-hour-old tweets lose ~30pts.
    time_decay = min(30, int(age_hours * 2.0))

    raw = tier_weight + engagement_bonus - time_decay + 5
    return max(1, min(100, raw))


# ── Tweet parsing helpers ─────────────────────────────────────────────────────


def _extract_urls_from_tweet(tweet: dict) -> list[str]:
    """Extract expanded URLs from a tweet's entities field.

    Twitter pre-expands t.co short links in the API response, so
    ``expanded_url`` gives us the destination without manual redirect-following.
    """
    entities = tweet.get("entities") or {}
    url_objects = entities.get("urls") or []
    result: list[str] = []
    for u in url_objects:
        expanded = u.get("expanded_url") or u.get("url") or ""
        if expanded:
            result.append(expanded)
    return result


def _tweet_age_hours(created_at: str) -> float:
    """Return tweet age in hours from its created_at ISO 8601 string."""
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return max(0.0, delta.total_seconds() / 3600.0)
    except Exception:
        return 12.0  # safe fallback if parsing fails


# ── Query builders ────────────────────────────────────────────────────────────


def _build_cohort_queries(accounts: Sequence[str]) -> list[str]:
    """Build Twitter recent-search query strings for a cohort of accounts.

    Twitter's query has a 512-character limit, so large cohorts are split
    into batches of ``_MAX_ACCOUNTS_PER_QUERY`` accounts each.
    """
    queries: list[str] = []
    accounts_list = list(accounts)
    for i in range(0, len(accounts_list), _MAX_ACCOUNTS_PER_QUERY):
        batch = accounts_list[i : i + _MAX_ACCOUNTS_PER_QUERY]
        from_clause = " OR ".join(f"from:{a}" for a in batch)
        queries.append(f"({from_clause}) {_QUERY_BASE_FILTERS}")
    return queries


def _build_topical_queries(terms: Sequence[str]) -> list[str]:
    """Build Twitter recent-search query strings for topical terms."""
    return [f"{term} {_QUERY_BASE_FILTERS}" for term in terms]


# ── Twitter API client ────────────────────────────────────────────────────────


def _call_recent_search(
    query: str,
    bearer_token: str,
    max_results: int,
    timeout: int,
) -> list[dict]:
    """Call Twitter v2 ``/tweets/search/recent`` and return tweet objects.

    Handles HTTP error codes gracefully:
    - 429 (rate limited): warn and return []
    - 401 (bad token): log error and return []
    - Other errors: warn and return []
    """
    params = {
        "query": query,
        "max_results": min(max_results, _TWITTER_MAX_RESULTS_CAP),
        "tweet.fields": "created_at,public_metrics,entities,author_id",
        "expansions": "author_id",
        "user.fields": "username,public_metrics",
    }
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "User-Agent": "blips-x-signal/1.0",
    }
    try:
        resp = requests.get(
            f"{_X_API_BASE}/tweets/search/recent",
            params=params,
            headers=headers,
            timeout=timeout,
        )
    except requests.Timeout:
        logger.warning("[x_signal] API request timed out for query=%r", query[:60])
        return []
    except requests.RequestException as exc:
        logger.warning("[x_signal] API request failed: %s", exc)
        return []

    if resp.status_code == 429:
        logger.warning("[x_signal] rate limited by Twitter API (429) — skipping remaining calls")
        return []
    if resp.status_code == 401:
        logger.error("[x_signal] Twitter API auth failed (401) — verify X_BEARER_TOKEN")
        return []
    if not resp.ok:
        logger.warning(
            "[x_signal] API returned HTTP %d for query=%r", resp.status_code, query[:60]
        )
        return []

    try:
        return resp.json().get("data") or []
    except Exception as exc:
        logger.warning("[x_signal] failed to parse API response: %s", exc)
        return []


# ── Main entry point ──────────────────────────────────────────────────────────


def fetch_x_signals(
    *,
    bearer_token: str,
    mode: str = "cohort",
    cohort_accounts: Optional[Sequence[str]] = None,
    query_terms: Optional[Sequence[str]] = None,
    limit: int = 50,
    min_score: int = 10,
    allowed_domains: Optional[Set[str]] = None,
    max_tweet_age_hours: int = 24,
    rate_limit_enabled: bool = True,
    request_timeout: int = 15,
    debug_logging: bool = False,
) -> List[SignalItem]:
    """Fetch signal URLs from X (Twitter) for the existing signal pipeline.

    Returns a list of ``SignalItem`` objects containing resolved article/video
    URLs. Twitter posts are never returned — only the URLs they embed.

    Args:
        bearer_token:       Twitter API v2 Bearer Token.
        mode:               ``cohort`` | ``query`` | ``mixed`` | ``off``.
        cohort_accounts:    Override default trusted account list.
        query_terms:        Override default topical search terms.
        limit:              Maximum SignalItems to return.
        min_score:          Minimum computed score; lower-scoring items dropped.
        allowed_domains:    Optional additional domain allowlist. When set, only
                            URLs on these domains are accepted (further narrows
                            the existing domain policy gate in the orchestrator).
        max_tweet_age_hours: Reject tweets older than this threshold.
        rate_limit_enabled: Sleep 1s between consecutive API calls.
        request_timeout:    Per-request HTTP timeout in seconds.
        debug_logging:      Log per-URL rejection reasons at DEBUG level.

    Returns:
        List of ``SignalItem`` (may be empty on failure; never raises).
    """
    if not bearer_token:
        logger.warning("[x_signal] X_BEARER_TOKEN not set — skipping X signal fetch")
        return []

    if mode == "off":
        logger.info("[x_signal] mode=off — skipping X signal fetch")
        return []

    accounts = cohort_accounts or list(_DEFAULT_COHORT_ACCOUNTS)
    terms = query_terms or list(_DEFAULT_QUERY_TERMS)

    # Build the list of (query_string, is_cohort) pairs.
    queries: list[tuple[str, bool]] = []
    if mode in ("cohort", "mixed"):
        for q in _build_cohort_queries(accounts):
            queries.append((q, True))
    if mode in ("query", "mixed"):
        for q in _build_topical_queries(terms):
            queries.append((q, False))

    if not queries:
        logger.warning("[x_signal] no queries built for mode=%s", mode)
        return []

    metrics = XSignalMetrics()

    # Distribute the per-run limit across queries. Over-fetch slightly so
    # rejected URLs don't starve the cap.
    per_query_limit = min(
        _TWITTER_MAX_RESULTS_CAP,
        max(10, (limit * 2) // max(1, len(queries)) + 10),
    )

    # Fetch tweets from all queries.
    all_tagged_tweets: list[tuple[dict, bool]] = []  # (tweet, is_cohort)
    rate_limited = False

    for i, (query, is_cohort) in enumerate(queries):
        if rate_limited:
            break
        if i > 0 and rate_limit_enabled:
            time.sleep(1.0)

        try:
            tweets = _call_recent_search(
                query=query,
                bearer_token=bearer_token,
                max_results=per_query_limit,
                timeout=request_timeout,
            )
        except Exception as exc:
            logger.warning("[x_signal] unexpected error during API call: %s", exc)
            metrics.errors.append(str(exc))
            tweets = []
        metrics.api_calls += 1

        if not tweets and i == 0 and metrics.api_calls == 1:
            # First call returned nothing — could be rate limit or bad token.
            # Continue trying other queries rather than bailing entirely.
            pass

        for tweet in tweets:
            all_tagged_tweets.append((tweet, is_cohort))

        logger.debug(
            "[x_signal] query=%r is_cohort=%s returned %d tweets",
            query[:60],
            is_cohort,
            len(tweets),
        )

    metrics.posts_seen = len(all_tagged_tweets)

    # Process tweets → SignalItems.
    seen_canonical_urls: set[str] = set()
    items: list[SignalItem] = []

    for tweet, is_cohort in all_tagged_tweets:
        if len(items) >= limit:
            break

        created_at = tweet.get("created_at", "")
        age_hours = _tweet_age_hours(created_at) if created_at else 12.0

        if age_hours > max_tweet_age_hours:
            if debug_logging:
                logger.debug(
                    "[x_signal] skip tweet age=%.1fh > max=%dh id=%s",
                    age_hours,
                    max_tweet_age_hours,
                    tweet.get("id", "?"),
                )
            continue

        raw_urls = _extract_urls_from_tweet(tweet)
        metrics.urls_extracted += len(raw_urls)

        public_metrics = tweet.get("public_metrics") or {}
        like_count = int(public_metrics.get("like_count") or 0)
        retweet_count = int(public_metrics.get("retweet_count") or 0)

        for raw_url in raw_urls:
            # Phase 1: Safety and content-type validation.
            if not _is_safe_content_url(raw_url, debug=debug_logging):
                metrics.urls_rejected += 1
                continue

            # Phase 2: URL normalization (strips tracking params, canonicalizes YT).
            canonical = normalize_url(raw_url)
            if not canonical:
                metrics.urls_rejected += 1
                continue

            # Phase 3: Optional domain allowlist (X-specific, additive restriction).
            if allowed_domains:
                host = (urlsplit(canonical).hostname or "").lower()
                if not any(
                    host == d or host.endswith(f".{d}") for d in allowed_domains
                ):
                    metrics.urls_rejected += 1
                    if debug_logging:
                        logger.debug(
                            "[x_signal] reject url=%s reason=not_in_allowlist host=%s",
                            canonical[:100],
                            host,
                        )
                    continue

            # Phase 4: Run-level deduplication (cross-query).
            if canonical in seen_canonical_urls:
                continue
            seen_canonical_urls.add(canonical)
            metrics.urls_resolved += 1

            # Phase 5: Score filter.
            score = compute_x_signal_score(
                in_cohort=is_cohort,
                like_count=like_count,
                retweet_count=retweet_count,
                age_hours=age_hours,
            )
            if score < min_score:
                metrics.urls_rejected += 1
                if debug_logging:
                    logger.debug(
                        "[x_signal] reject url=%s reason=score_too_low score=%d min=%d",
                        canonical[:100],
                        score,
                        min_score,
                    )
                continue

            items.append(
                SignalItem(
                    raw_url=raw_url,
                    signal_source=SignalSource.X_SIGNAL,
                    raw_title=None,
                    signal_score=score,
                )
            )

    metrics.items_returned = len(items)

    logger.info(
        "[x_signal] mode=%s api_calls=%d posts_seen=%d "
        "urls_extracted=%d resolved=%d rejected=%d returned=%d errors=%d",
        mode,
        metrics.api_calls,
        metrics.posts_seen,
        metrics.urls_extracted,
        metrics.urls_resolved,
        metrics.urls_rejected,
        metrics.items_returned,
        len(metrics.errors),
    )

    return items
