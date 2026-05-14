"""URL canonicalization helpers for clustering.

Distinct from ``app/ingestion/url_normalizer.py``, which handles tracking-param
dedup at insert time and preserves the query string for non-tracking params.
This module strips *all* query params and fragments so that two URLs for the
same article path (e.g. one fetched via RSS with ``?utm_source=rss`` and one
fetched via a tweet link with ``?ref=newsletter``) collapse to the same key for
cluster matching.

Only articles are candidates (videos are clustered by entity/title already and
YouTube URLs need special handling).
"""

from __future__ import annotations

from urllib.parse import urlsplit


def canonical_cluster_url(url: str | None) -> str | None:
    """Return a stripped ``"{host}/{path}"`` key suitable for cluster matching.

    Rules applied (in order):
    - ``None`` / empty string → returns ``None``
    - Split with ``urlsplit``
    - Strip ``www.`` and ``amp.`` prefixes from host
    - Drop all query params and fragments
    - Strip trailing ``/amp`` or ``/amp/`` suffix from path
    - Strip trailing slash (except bare root paths)
    - Return ``"{host}{path}"`` (no scheme, no query, no fragment)

    Examples::

        canonical_cluster_url("https://www.techcrunch.com/2024/01/01/story/?utm_source=rss")
        # "techcrunch.com/2024/01/01/story"

        canonical_cluster_url("https://amp.theverge.com/2024/01/01/story/amp/")
        # "theverge.com/2024/01/01/story"

        canonical_cluster_url(None)
        # None
    """
    if not url:
        return None

    try:
        parts = urlsplit(url)
    except Exception:
        return None

    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("amp."):
        host = host[4:]

    if not host:
        return None

    path = parts.path or ""

    # Strip AMP suffix variants
    path_lower = path.lower()
    if path_lower.endswith("/amp/"):
        path = path[:-5]
    elif path_lower.endswith("/amp"):
        path = path[:-4]

    # Strip trailing slash (unless it's the bare root)
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    return f"{host}{path}"
