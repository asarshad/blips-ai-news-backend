"""Metadata extraction from HTML with editorial image ranking.

Parses page metadata with BeautifulSoup and prefers article hero imagery
over generic social/share art when the body exposes a stronger candidate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.extraction.normalize import (
    is_suspicious_image_url,
    make_absolute_url,
    validate_image_url,
)

logger = get_logger(__name__)


@dataclass
class PageMetadata:
    """Extracted page metadata."""

    canonical_url: Optional[str] = None
    title: Optional[str] = None
    image_url: Optional[str] = None
    image_source: str = "none"  # og | twitter | body | rss | none
    image_confidence: str = "none"  # high | medium | low | none
    image_suspicious: bool = False
    description: Optional[str] = None
    published_at_str: Optional[str] = None  # raw from meta tags


@dataclass(frozen=True)
class _ImageCandidate:
    url: str
    source: str
    score: float
    suspicious: bool = False


_GENERIC_URL_KEYWORDS = (
    "logo",
    "icon",
    "avatar",
    "placeholder",
    "sprite",
    "spacer",
    "blank",
    "nojsimg",
    "no-js",
    "social-share",
    "social_share",
    "socialshare",
    "share-image",
    "share_image",
    "meta-image",
    "meta_image",
    "og-image",
    "og_image",
    "open-graph",
    "opengraph",
    "default-image",
    "default_image",
    "site-image",
    "site_image",
    "brand-image",
    "brand_image",
    # User-generated/uploaded content is not a reliable editorial hero image.
    # Also catches proxied CDN URLs (e.g. Yahoo image proxy) wrapping user-uploaded paths.
    "user-uploaded",
    "user_uploaded",
    # Next.js image optimisation proxy URLs (/_next/image?url=...) are tied to
    # the origin server and often blocked for third-party hotlinking.
    # Flag them so the backfill re-fetches; validate_image_url will unwrap to
    # the direct asset URL on the fresh fetch.
    "/_next/image",
    # Netlify Image CDN and Gatsby Static Image CDN have the same origin-specific
    # restriction pattern — flag for the same backfill-and-unwrap treatment.
    "/.netlify/images",
    "/_gatsby/image",
)
_EDITORIAL_URL_KEYWORDS = (
    "hero",
    "feature",
    "featured",
    "cover",
    "lead",
    "story",
    "article",
    "post",
    "header-image",
    "header_image",
)
_NON_EDITORIAL_CONTEXT_KEYWORDS = (
    "logo",
    "icon",
    "avatar",
    "badge",
    "emoji",
    "favicon",
    "placeholder",
    "sprite",
    "tracking",
    "pixel",
    "advert",
    "sponsor",
    "promo",
    "nav",
    "footer",
    "comment",
    "share",
    "related",
    "author",
    "byline",
    "thumbnail",
    "thumb",
    "loop-card",
    "card-block",
    "storycard",
    "rightrail",
    "stayconnected",
    "footer__storycard",
    "footer-story-card",
)
_EDITORIAL_CONTEXT_KEYWORDS = (
    "hero",
    "feature",
    "featured",
    "cover",
    "lead",
    "article-image",
    "article_image",
    "post-image",
    "post_image",
    "story-image",
    "story_image",
    "featured-image",
    "featured_image",
    "post-featured-image",
    "post_featured_image",
    "wp-block-post-featured-image",
    "contentarticleheader__image",
)

_STRONG_EDITORIAL_CONTEXT_KEYWORDS = (
    "featured-image",
    "featured_image",
    "post-featured-image",
    "post_featured_image",
    "wp-block-post-featured-image",
    "contentarticleheader__image",
)

_SRCSET_CANDIDATE_RE = re.compile(
    r"""
    (?:^|,\s*)            # Candidate boundary
    (?P<url>\S+?)         # URL token (may contain commas in query params)
    \s+
    (?P<descriptor>\d+(?:\.\d+)?[wx])
    (?=\s*(?:,|$))
    """,
    re.VERBOSE,
)


def extract_metadata(html: str, source_url: str) -> PageMetadata:
    """Extract metadata from an HTML page's <head>.

    Args:
        html: Full HTML content.
        source_url: The URL the page was fetched from (used for absolutifying).

    Returns:
        PageMetadata with best-effort fields populated.
    """
    meta = PageMetadata()

    if not html:
        return meta

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        # lxml not available or parse error — fallback to html.parser
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return meta

    head = soup.find("head") or soup

    # ── Canonical URL ─────────────────────────────────────────────────────
    canonical_tag = head.find("link", rel="canonical")
    if canonical_tag and canonical_tag.get("href"):
        raw = canonical_tag["href"].strip()
        meta.canonical_url = make_absolute_url(raw, source_url)

    # ── Title (priority: og:title → twitter:title → <title>) ─────────────
    og_title = _meta_content(head, prop="og:title")
    tw_title = _meta_content(head, attrs={"name": "twitter:title"})
    html_title = head.find("title")

    meta.title = og_title or tw_title or (html_title.get_text(strip=True) if html_title else None)

    # ── Description ───────────────────────────────────────────────────────
    og_desc = _meta_content(head, prop="og:description")
    meta_desc = _meta_content(head, attrs={"name": "description"})
    meta.description = og_desc or meta_desc

    # ── Image URL (rank head metadata against body/editorial candidates) ──
    head_candidates = []
    og_image = _meta_content(head, prop="og:image")
    tw_image = _meta_content(head, attrs={"name": "twitter:image"})

    if og_image:
        candidate = _build_head_image_candidate(og_image, "og", source_url)
        if candidate:
            head_candidates.append(candidate)
    if tw_image:
        candidate = _build_head_image_candidate(tw_image, "twitter", source_url)
        if candidate:
            head_candidates.append(candidate)

    best_image = _select_best_image_candidate(
        head_candidates,
        _extract_best_editorial_image(soup, source_url, source_label="body"),
    )
    if best_image:
        meta.image_url = best_image.url
        meta.image_source = best_image.source
        meta.image_suspicious = best_image.suspicious
        if best_image.score >= 72:
            meta.image_confidence = "high"
        elif best_image.score >= 52:
            meta.image_confidence = "medium"
        else:
            meta.image_confidence = "low"

    # ── Published date (best effort from meta tags) ───────────────────────
    for attr_name in [
        "article:published_time",
        "datePublished",
        "date",
        "pubdate",
    ]:
        val = _meta_content(head, prop=attr_name) or _meta_content(head, attrs={"name": attr_name})
        if val:
            meta.published_at_str = val.strip()
            break

    return meta


def extract_best_image_from_fragment(fragment_html: str, source_url: str) -> Optional[str]:
    """Extract the strongest editorial image from an HTML fragment."""
    if not fragment_html:
        return None

    try:
        soup = BeautifulSoup(fragment_html, "html.parser")
    except Exception:
        return None

    candidate = _extract_best_editorial_image(soup, source_url, source_label="body")
    return candidate.url if candidate else None


def is_probably_generic_image_url(url: Optional[str]) -> bool:
    """Return True when the URL looks like reusable social/share/site art."""
    if not url or not url.strip():
        return False

    parsed = urlparse(url.strip())
    haystack = " ".join(
        part.lower()
        for part in (
            parsed.path or "",
            parsed.query or "",
            parsed.fragment or "",
        )
        if part
    )
    if not haystack:
        return False

    if any(keyword in haystack for keyword in _GENERIC_URL_KEYWORDS):
        return True

    filename = (parsed.path.rsplit("/", 1)[-1] or "").lower()
    if filename in {"logo.png", "logo.jpg", "logo.jpeg", "logo.webp"}:
        return True

    return False


def _meta_content(
    tag, *, prop: Optional[str] = None, attrs: Optional[dict] = None
) -> Optional[str]:
    """Get the ``content`` attribute from a <meta> tag, or ``None``."""
    try:
        if prop:
            el = tag.find("meta", property=prop)
        elif attrs:
            el = tag.find("meta", attrs=attrs)
        else:
            return None
        if el and el.get("content"):
            return el["content"].strip()
    except Exception:
        pass
    return None


def _build_head_image_candidate(
    raw_url: str, source: str, source_url: str
) -> Optional[_ImageCandidate]:
    """Build a scored candidate from OG/Twitter metadata."""
    absolute = make_absolute_url(raw_url, source_url)
    validated = validate_image_url(absolute)
    if not validated:
        return None

    source_host = (urlparse(source_url).hostname or "").lower()
    candidate_host = (urlparse(validated).hostname or "").lower()
    score = 56.0
    if source == "og":
        score += 8.0
    elif source == "twitter":
        score += 2.0
    if (
        candidate_host
        and source_host
        and (
            candidate_host == source_host
            or candidate_host.endswith(f".{source_host}")
            or source_host.endswith(f".{candidate_host}")
        )
    ):
        score += 10.0
    elif candidate_host and source_host:
        score -= 6.0
    if not is_probably_generic_image_url(validated):
        score += 6.0
    else:
        score -= 24.0
    if any(keyword in validated.lower() for keyword in _EDITORIAL_URL_KEYWORDS):
        score += 4.0
    suspicious = is_suspicious_image_url(validated)
    if suspicious:
        score -= 30.0

    return _ImageCandidate(
        url=validated,
        source=source,
        score=score,
        suspicious=suspicious,
    )


def _extract_best_editorial_image(
    soup: BeautifulSoup, source_url: str, *, source_label: str
) -> Optional[_ImageCandidate]:
    """Return the highest-scoring editorial image found in the page body."""
    best_candidate: Optional[_ImageCandidate] = None
    seen_urls: set[str] = set()
    seen_roots: set[int] = set()
    for root in (soup.find("article"), soup.find("main"), soup.body, soup):
        if root is None or id(root) in seen_roots:
            continue
        seen_roots.add(id(root))
        for position, tag in enumerate(root.find_all(["picture", "img"], limit=20)):
            candidate = _tag_candidate_url(tag)
            if not candidate or not _looks_like_editorial_image(tag, candidate):
                continue
            absolute = make_absolute_url(candidate, source_url)
            validated = validate_image_url(absolute)
            if not validated or validated in seen_urls:
                continue
            seen_urls.add(validated)
            score = _score_body_image_candidate(tag, validated, position)
            candidate_obj = _ImageCandidate(
                url=validated,
                source=source_label,
                score=score,
                suspicious=is_suspicious_image_url(validated),
            )
            if best_candidate is None or candidate_obj.score > best_candidate.score:
                best_candidate = candidate_obj

    return best_candidate


def _select_best_image_candidate(
    head_candidates: list[_ImageCandidate], body_candidate: Optional[_ImageCandidate]
) -> Optional[_ImageCandidate]:
    """Choose the strongest image candidate across metadata and body signals."""
    if not head_candidates and not body_candidate:
        return None

    best_head = max(head_candidates, key=lambda candidate: candidate.score, default=None)
    if body_candidate and best_head:
        if best_head.source == "og" and best_head.score >= body_candidate.score - 4.0:
            return best_head

    candidates = list(head_candidates)
    if body_candidate:
        candidates.append(body_candidate)

    candidates.sort(
        key=lambda candidate: (
            candidate.score,
            1 if candidate.source == "body" else 0,
        ),
        reverse=True,
    )
    return candidates[0]


def _tag_candidate_url(tag) -> Optional[str]:
    """Pick the strongest URL-like attribute from a picture/img tag."""
    if getattr(tag, "name", None) == "picture":
        for source in tag.find_all("source"):
            if candidate := _attribute_candidate_url(source):
                return candidate
        img = tag.find("img")
        if img and (candidate := _attribute_candidate_url(img)):
            return candidate
        return None

    return _attribute_candidate_url(tag)


def _attribute_candidate_url(tag) -> Optional[str]:
    """Pick the best candidate URL from a single HTML node."""
    for attr in (
        "data-src",
        "data-lazy-src",
        "data-original",
        "data-image",
        "data-url",
        "data-srcset",
        "srcset",
        "src",
    ):
        value = tag.get(attr)
        if not value or not str(value).strip():
            continue
        if attr.endswith("srcset"):
            if candidate := _best_srcset_candidate(str(value)):
                return candidate
            continue
        return str(value).strip()

    return None


def _best_srcset_candidate(srcset: str) -> Optional[str]:
    """Return the largest-width candidate from an srcset string."""
    best_url = None
    best_width = -1

    matches = list(_SRCSET_CANDIDATE_RE.finditer(str(srcset)))
    if matches:
        for match in matches:
            candidate_url = match.group("url").strip()
            descriptor = match.group("descriptor").strip().lower()
            width = 0
            if descriptor.endswith("w"):
                try:
                    width = int(float(descriptor[:-1]))
                except ValueError:
                    width = 0
            if width >= best_width:
                best_width = width
                best_url = candidate_url
        return best_url

    for part in str(srcset).split(","):
        chunk = part.strip()
        if not chunk:
            continue
        pieces = [p for p in chunk.split(" ") if p]
        candidate_url = pieces[0].strip()
        width = 0
        for piece in pieces[1:]:
            if piece.endswith("w"):
                try:
                    width = int(piece[:-1])
                except ValueError:
                    width = 0
                break
        if width >= best_width:
            best_width = width
            best_url = candidate_url
    return best_url


def _looks_like_editorial_image(tag, candidate_url: str) -> bool:
    """Filter obvious logos, icons, placeholders, and tiny decorative images."""
    context_tag = _image_context_tag(tag)
    haystack = " ".join(
        filter(
            None,
            [
                candidate_url,
                context_tag.get("alt"),
                context_tag.get("id"),
                " ".join(context_tag.get("class", [])),
                context_tag.get("aria-label"),
                _ancestor_context(context_tag),
            ],
        )
    ).lower()
    strong_editorial_context = any(
        keyword in haystack for keyword in _STRONG_EDITORIAL_CONTEXT_KEYWORDS
    )
    non_editorial_hits = [
        keyword for keyword in _NON_EDITORIAL_CONTEXT_KEYWORDS if keyword in haystack
    ]
    if non_editorial_hits:
        benign_thumbnail_hits = {
            keyword for keyword in non_editorial_hits if keyword in {"thumb", "thumbnail"}
        }
        if not strong_editorial_context or len(benign_thumbnail_hits) != len(non_editorial_hits):
            return False
    if "post-thumbnail" in haystack and strong_editorial_context:
        non_editorial_hits = [
            keyword for keyword in non_editorial_hits if keyword not in {"thumb", "thumbnail"}
        ]
    if non_editorial_hits:
        return False
    if is_probably_generic_image_url(candidate_url) and not any(
        keyword in haystack for keyword in _EDITORIAL_CONTEXT_KEYWORDS
    ):
        return False

    width = _parse_dimension(context_tag.get("width")) or _parse_dimension(
        context_tag.get("data-width")
    )
    height = _parse_dimension(context_tag.get("height")) or _parse_dimension(
        context_tag.get("data-height")
    )
    if width is not None and width < 160:
        return False
    if height is not None and height < 120:
        return False

    return True


def _score_body_image_candidate(tag, candidate_url: str, position: int) -> float:
    """Score a body-image candidate; higher is better."""
    context_tag = _image_context_tag(tag)
    score = 70.0
    width = _parse_dimension(context_tag.get("width")) or _parse_dimension(
        context_tag.get("data-width")
    )
    height = _parse_dimension(context_tag.get("height")) or _parse_dimension(
        context_tag.get("data-height")
    )
    if width:
        score += min(width, 1600) / 80.0
    if height:
        score += min(height, 1200) / 120.0

    if position < 3:
        score += 12.0 - (position * 3.0)
    else:
        score -= min(18.0, (position - 2) * 2.5)

    context = " ".join(
        filter(
            None,
            [
                candidate_url,
                context_tag.get("alt"),
                context_tag.get("id"),
                " ".join(context_tag.get("class", [])),
                context_tag.get("aria-label"),
                _ancestor_context(context_tag),
            ],
        )
    ).lower()
    if any(keyword in context for keyword in _EDITORIAL_CONTEXT_KEYWORDS):
        score += 12.0
    if any(keyword in context for keyword in ("thumb", "thumbnail", "gallery", "inline")):
        score -= 12.0
    if is_probably_generic_image_url(candidate_url):
        score -= 30.0

    return score


def _ancestor_context(tag) -> str:
    """Collect a small amount of ancestor context for image heuristics."""
    parts = []
    current = getattr(tag, "parent", None)
    hops = 0
    while current is not None and hops < 3:
        parts.extend(
            filter(
                None,
                [
                    getattr(current, "name", None),
                    current.get("id"),
                    " ".join(current.get("class", [])),
                ],
            )
        )
        current = getattr(current, "parent", None)
        hops += 1
    return " ".join(str(part) for part in parts if part)


def _image_context_tag(tag):
    """Use the nested <img> for picture nodes when scoring editorial signals."""
    if getattr(tag, "name", None) == "picture":
        return tag.find("img") or tag
    return tag


def _parse_dimension(value) -> Optional[int]:
    """Parse integer dimensions from HTML width/height attributes."""
    if value is None:
        return None
    try:
        return int(str(value).strip().replace("px", ""))
    except (TypeError, ValueError):
        return None
