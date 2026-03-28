"""Tests for app.extraction.metadata — HTML head metadata extraction."""

from app.extraction.metadata import PageMetadata, extract_metadata, is_probably_generic_image_url


def _html_with_head(head_content: str) -> str:
    """Build a minimal HTML doc with the given <head> content."""
    return f"""<!DOCTYPE html>
<html><head>{head_content}</head><body><p>Body</p></body></html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# Canonical URL
# ═══════════════════════════════════════════════════════════════════════════════


class TestCanonicalUrl:
    def test_absolute_canonical(self):
        html = _html_with_head('<link rel="canonical" href="https://example.com/article/1" />')
        meta = extract_metadata(html, "https://example.com/article/1?utm=xyz")
        assert meta.canonical_url == "https://example.com/article/1"

    def test_relative_canonical_resolved(self):
        html = _html_with_head('<link rel="canonical" href="/article/1" />')
        meta = extract_metadata(html, "https://example.com/section/old")
        assert meta.canonical_url == "https://example.com/article/1"

    def test_no_canonical_returns_none(self):
        html = _html_with_head("<title>No Canonical</title>")
        meta = extract_metadata(html, "https://example.com/page")
        assert meta.canonical_url is None

    def test_empty_canonical_href(self):
        html = _html_with_head('<link rel="canonical" href="" />')
        meta = extract_metadata(html, "https://example.com/page")
        # Empty href should not produce canonical
        assert meta.canonical_url is None


# ═══════════════════════════════════════════════════════════════════════════════
# Title
# ═══════════════════════════════════════════════════════════════════════════════


class TestTitleExtraction:
    def test_og_title_preferred(self):
        html = _html_with_head(
            '<meta property="og:title" content="OG Title" />'
            '<meta name="twitter:title" content="Twitter Title" />'
            "<title>HTML Title</title>"
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.title == "OG Title"

    def test_twitter_title_fallback(self):
        html = _html_with_head(
            '<meta name="twitter:title" content="Twitter Title" /><title>HTML Title</title>'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.title == "Twitter Title"

    def test_html_title_fallback(self):
        html = _html_with_head("<title>HTML Title</title>")
        meta = extract_metadata(html, "https://example.com")
        assert meta.title == "HTML Title"

    def test_no_title(self):
        html = _html_with_head("")
        meta = extract_metadata(html, "https://example.com")
        assert meta.title is None


# ═══════════════════════════════════════════════════════════════════════════════
# Image URL
# ═══════════════════════════════════════════════════════════════════════════════


class TestImageExtraction:
    def test_nojs_placeholder_image_marked_generic(self):
        assert is_probably_generic_image_url("https://www.cbc.ca/a/assets/nojsimg.gif")

    def test_user_uploaded_image_marked_generic(self):
        # Yahoo proxy wrapping a user-uploaded cloudfront image — seen on real engadget articles
        url = (
            "https://s.yimg.com/uu/api/res/1.2/ABC--/hash/"
            "https://d29szjachogqwa.cloudfront.net/images/user-uploaded/ss-6.jpg"
        )
        assert is_probably_generic_image_url(url)

    def test_user_uploaded_underscore_variant_marked_generic(self):
        assert is_probably_generic_image_url(
            "https://cdn.example.com/images/user_uploaded/photo.jpg"
        )

    def test_nextjs_image_proxy_url_marked_generic(self):
        # Stored /_next/image URLs are flagged so the repair backfill re-fetches
        # them; validate_image_url will unwrap to the direct asset URL on re-fetch.
        from urllib.parse import quote

        inner = "https://images.ctfassets.net/abc/photo.png"
        proxy = f"https://venturebeat.com/_next/image?url={quote(inner)}&w=3840&q=85"
        assert is_probably_generic_image_url(proxy)

    def test_netlify_image_cdn_url_marked_generic(self):
        from urllib.parse import quote

        proxy = f"https://samhenri.gold/.netlify/images?url={quote('_astro/post.jpg')}&w=1200"
        assert is_probably_generic_image_url(proxy)

    def test_gatsby_image_cdn_url_marked_generic(self):
        from urllib.parse import quote

        proxy = (
            f"https://worksinprogress.co/_gatsby/image/abc/def/photo.png"
            f"?u={quote('https://worksinprogress.co/wip-image/uploads/2026/photo.jpg')}"
        )
        assert is_probably_generic_image_url(proxy)

    def test_engadget_amp_proposed_image_marked_generic(self):
        assert is_probably_generic_image_url(
            "https://s.yimg.com/kw/assets/engadget-amp-proposed.png"
        )

    def test_og_image_preferred(self):
        html = _html_with_head(
            '<meta property="og:image" content="https://cdn.example.com/og.jpg" />'
            '<meta name="twitter:image" content="https://cdn.example.com/tw.jpg" />'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.image_url == "https://cdn.example.com/og.jpg"
        assert meta.image_source == "og"

    def test_twitter_image_fallback(self):
        html = _html_with_head(
            '<meta name="twitter:image" content="https://cdn.example.com/tw.jpg" />'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.image_url == "https://cdn.example.com/tw.jpg"
        assert meta.image_source == "twitter"

    def test_relative_image_made_absolute(self):
        html = _html_with_head('<meta property="og:image" content="/images/hero.jpg" />')
        meta = extract_metadata(html, "https://example.com/article")
        assert meta.image_url == "https://example.com/images/hero.jpg"
        assert meta.image_source == "og"

    def test_data_uri_image_rejected(self):
        html = _html_with_head('<meta property="og:image" content="data:image/png;base64,abc" />')
        meta = extract_metadata(html, "https://example.com")
        assert meta.image_url is None
        assert meta.image_source == "none"

    def test_tracker_beacon_og_image_rejected(self):
        html = _html_with_head(
            '<meta property="og:image" content="https://www.google-analytics.com/g/collect?v=2&tid=G-TEST&cid=123" />'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.image_url is None
        assert meta.image_source == "none"

    def test_no_image(self):
        html = _html_with_head("<title>No Image</title>")
        meta = extract_metadata(html, "https://example.com")
        assert meta.image_url is None
        assert meta.image_source == "none"

    def test_body_image_fallback_when_head_metadata_missing(self):
        html = """<!DOCTYPE html>
<html>
  <head><title>Body Image</title></head>
  <body>
    <article>
      <img src="/images/hero.jpg" width="1280" height="720" alt="Hero image" />
    </article>
  </body>
</html>"""
        meta = extract_metadata(html, "https://example.com/story")
        assert meta.image_url == "https://example.com/images/hero.jpg"
        assert meta.image_source == "body"

    def test_body_image_skips_logo_and_uses_next_editorial_image(self):
        html = """<!DOCTYPE html>
<html>
  <head><title>Logo First</title></head>
  <body>
    <article>
      <img src="/assets/logo.png" width="96" height="96" class="site-logo" alt="Site logo" />
      <img
        data-srcset="/images/hero-1280.jpg 1280w, /images/hero-640.jpg 640w"
        width="1280"
        height="720"
        alt="Feature photo"
      />
    </article>
  </body>
</html>"""
        meta = extract_metadata(html, "https://example.com/story")
        assert meta.image_url == "https://example.com/images/hero-1280.jpg"
        assert meta.image_source == "body"

    def test_body_image_beats_generic_head_share_image(self):
        html = """<!DOCTYPE html>
<html>
  <head>
    <title>Generic Share Art</title>
    <meta property="og:image" content="https://cdn.example.com/social-share.png" />
  </head>
  <body>
    <article>
      <img src="/images/article-hero.jpg" width="1280" height="720" alt="Feature image" />
    </article>
  </body>
</html>"""
        meta = extract_metadata(html, "https://example.com/story")
        assert meta.image_url == "https://example.com/images/article-hero.jpg"
        assert meta.image_source == "body"

    def test_same_site_og_beats_later_body_inline_image(self):
        html = """<!DOCTYPE html>
<html>
  <head>
    <title>Strong OG</title>
    <meta property="og:image" content="https://example.com/images/og-hero.jpg" />
  </head>
  <body>
    <article>
      <p>Intro paragraph</p>
      <img src="/images/gallery-01.jpg" width="900" height="600" alt="gallery inline image" />
    </article>
  </body>
</html>"""
        meta = extract_metadata(html, "https://example.com/story")
        assert meta.image_url == "https://example.com/images/og-hero.jpg"
        assert meta.image_source == "og"
        assert meta.image_confidence == "high"

    def test_body_image_uses_picture_source_before_placeholder_src(self):
        html = """<!DOCTYPE html>
<html>
  <head><title>Picture Hero</title></head>
  <body>
    <article>
      <picture class="article-image">
        <source
          srcset="/images/hero-640.jpg 640w, /images/hero-1600.jpg 1600w"
          type="image/jpeg"
        />
        <img
          src="data:image/gif;base64,R0lGODlhAQABAAAAACw="
          width="1600"
          height="900"
          alt="Hero image"
        />
      </picture>
    </article>
  </body>
</html>"""
        meta = extract_metadata(html, "https://example.com/story")
        assert meta.image_url == "https://example.com/images/hero-1600.jpg"
        assert meta.image_source == "body"

    def test_body_image_keeps_featured_image_over_related_loop_cards(self):
        html = """<!DOCTYPE html>
<html>
  <head><title>Featured Article</title></head>
  <body>
    <article>
      <figure class="wp-block-post-featured-image">
        <img
          src="/images/featured.jpg"
          width="1024"
          height="683"
          class="attachment-post-thumbnail size-post-thumbnail wp-post-image"
          alt="Primary feature image"
        />
      </figure>
      <figure class="loop-card__figure">
        <img
          src="/images/related-1.jpg"
          width="488"
          height="375"
          class="attachment-card-block-16x9 size-card-block-16x9 wp-post-image"
          alt="Related story image"
        />
      </figure>
      <figure class="loop-card__figure">
        <img
          src="/images/related-2.jpg"
          width="666"
          height="375"
          class="attachment-card-block-16x9 size-card-block-16x9 wp-post-image"
          alt="Another related story image"
        />
      </figure>
    </article>
  </body>
</html>"""
        meta = extract_metadata(html, "https://example.com/story")
        assert meta.image_url == "https://example.com/images/featured.jpg"
        assert meta.image_source == "body"

    def test_body_image_srcset_handles_query_param_commas(self):
        html = """<!DOCTYPE html>
<html>
  <head><title>Srcset commas</title></head>
  <body>
    <article>
      <div class="contentArticleHeader__image">
        <img
          src="https://cdn.example.com/hero.jpg"
          srcset="https://cdn.example.com/hero.jpg?fit=720,480 720w,https://cdn.example.com/hero.jpg?fit=1456,818 1456w,https://cdn.example.com/hero.jpg?fit=2252,1266 2252w"
          alt="Lead illustration"
        />
      </div>
    </article>
  </body>
</html>"""
        meta = extract_metadata(html, "https://example.com/story")
        assert meta.image_url == "https://cdn.example.com/hero.jpg?fit=2252,1266"
        assert meta.image_source == "body"


# ═══════════════════════════════════════════════════════════════════════════════
# Published date
# ═══════════════════════════════════════════════════════════════════════════════


class TestPublishedDate:
    def test_article_published_time(self):
        html = _html_with_head(
            '<meta property="article:published_time" content="2024-06-15T10:30:00Z" />'
        )
        meta = extract_metadata(html, "https://example.com")
        assert meta.published_at_str == "2024-06-15T10:30:00Z"

    def test_date_published(self):
        html = _html_with_head('<meta name="datePublished" content="2024-01-01" />')
        meta = extract_metadata(html, "https://example.com")
        assert meta.published_at_str == "2024-01-01"

    def test_no_date(self):
        html = _html_with_head("<title>No Date</title>")
        meta = extract_metadata(html, "https://example.com")
        assert meta.published_at_str is None


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetadataEdgeCases:
    def test_empty_html(self):
        meta = extract_metadata("", "https://example.com")
        assert meta.canonical_url is None
        assert meta.title is None
        assert meta.image_url is None

    def test_garbage_html(self):
        meta = extract_metadata("<<<not html at all>>>", "https://example.com")
        # Should not raise, returns defaults
        assert isinstance(meta, PageMetadata)

    def test_description_extraction(self):
        html = _html_with_head('<meta property="og:description" content="OG description here" />')
        meta = extract_metadata(html, "https://example.com")
        assert meta.description == "OG description here"
