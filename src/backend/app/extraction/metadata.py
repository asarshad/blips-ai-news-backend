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
    image_source: str = "none"  # og | twitter | rss | none
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

    meta.title = (
        og_title
        or tw_title
        or (html_title.get_text(strip=True) if html_title else None)
    )

    # ── Description ───────────────────────────────────────────────────────
    og_desc = _meta_content(head, prop="og:description")
    meta_desc = _meta_content(head, attrs={"name": "description"})
    meta.description = og_desc or meta_desc

    # ── Image URL (priority: og:image → twitter:image) ───────────────────
    og_image = _meta_content(head, prop="og:image")
    tw_image = _meta_content(head, attrs={"name": "twitter:image"})

    raw_image = og_image or tw_image
    if raw_image:
        absolute = make_absolute_url(raw_image, source_url)
        validated = validate_image_url(absolute)
        if validated:
            meta.image_url = validated
            meta.image_source = "og" if og_image else "twitter"

    # ── Published date (best effort from meta tags) ───────────────────────
    for attr_name in [
        "article:published_time",
        "datePublished",
        "date",
        "pubdate",
    ]:
        val = _meta_content(head, prop=attr_name) or _meta_content(
            head, attrs={"name": attr_name}
        )
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
