"""Metadata extraction from HTML: canonical URL, title, og:image, twitter:image.

Parses <head> with BeautifulSoup (lxml parser) to extract:
- canonical_url: <link rel="canonical"> else source_url
- title: og:title → twitter:title → <title>
- image_url: og:image → twitter:image → RSS metadata
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.extraction.normalize import make_absolute_url, validate_image_url

logger = get_logger(__name__)


@dataclass
class PageMetadata:
    """Extracted page metadata."""

    canonical_url: Optional[str] = None
    title: Optional[str] = None
    image_url: Optional[str] = None
    image_source: str = "none"  # og | twitter | body | rss | none
    description: Optional[str] = None
    published_at_str: Optional[str] = None  # raw from meta tags


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

    # ── Image URL (priority: og:image → twitter:image → body image) ──────
    og_image = _meta_content(head, prop="og:image")
    tw_image = _meta_content(head, attrs={"name": "twitter:image"})

    raw_image = og_image or tw_image
    if raw_image:
        absolute = make_absolute_url(raw_image, source_url)
        validated = validate_image_url(absolute)
        if validated:
            meta.image_url = validated
            meta.image_source = "og" if og_image else "twitter"
    elif body_image := _extract_body_image(soup, source_url):
        meta.image_url = body_image
        meta.image_source = "body"

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


def _extract_body_image(soup: BeautifulSoup, source_url: str) -> Optional[str]:
    """Return the first likely editorial image found in the page body."""
    seen_roots: set[int] = set()
    for root in (soup.find("article"), soup.find("main"), soup.body, soup):
        if root is None or id(root) in seen_roots:
            continue
        seen_roots.add(id(root))
        for img in root.find_all("img"):
            candidate = _img_candidate_url(img)
            if not candidate or not _looks_like_editorial_image(img, candidate):
                continue
            absolute = make_absolute_url(candidate, source_url)
            validated = validate_image_url(absolute)
            if validated:
                return validated
    return None


def _img_candidate_url(img) -> Optional[str]:
    """Pick the best URL-like attribute from an <img> tag."""
    for attr in ("src", "data-src", "data-lazy-src", "data-original", "data-image"):
        value = img.get(attr)
        if value and str(value).strip():
            return str(value).strip()

    for attr in ("srcset", "data-srcset"):
        value = img.get(attr)
        if value and str(value).strip():
            first_candidate = str(value).split(",")[0].strip().split(" ")[0].strip()
            if first_candidate:
                return first_candidate

    return None


def _looks_like_editorial_image(img, candidate_url: str) -> bool:
    """Filter obvious logos, icons, placeholders, and tiny decorative images."""
    haystack = " ".join(
        filter(
            None,
            [
                candidate_url,
                img.get("alt"),
                img.get("id"),
                " ".join(img.get("class", [])),
                img.get("aria-label"),
            ],
        )
    ).lower()
    excluded_keywords = (
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
        "banner",
    )
    if any(keyword in haystack for keyword in excluded_keywords):
        return False

    width = _parse_dimension(img.get("width"))
    height = _parse_dimension(img.get("height"))
    if width is not None and width < 160:
        return False
    if height is not None and height < 120:
        return False

    return True


def _parse_dimension(value) -> Optional[int]:
    """Parse integer dimensions from HTML width/height attributes."""
    if value is None:
        return None
    try:
        return int(str(value).strip().replace("px", ""))
    except (TypeError, ValueError):
        return None
