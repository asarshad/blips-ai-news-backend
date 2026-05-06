"""Unit tests for the source-branded article placeholder."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import article_image_placeholder as placeholder_module
from app.services.article_image_placeholder import (
    PLACEHOLDER_URL_MARKER,
    apply_source_placeholder,
    build_source_placeholder_url,
    is_placeholder_image_url,
    render_source_placeholder_png,
    render_source_placeholder_svg,
    should_apply_placeholder,
)


def _make_settings(
    *,
    enabled: bool = True,
    min_age_minutes: int = 30,
    base_url: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        ARTICLE_IMAGE_PLACEHOLDER_ENABLED=enabled,
        ARTICLE_IMAGE_PLACEHOLDER_MIN_AGE_MINUTES=min_age_minutes,
        API_PUBLIC_BASE_URL=base_url,
    )


@pytest.fixture
def _with_settings(monkeypatch):
    def _apply(**overrides):
        monkeypatch.setattr(placeholder_module, "_SETTINGS", _make_settings(**overrides))

    return _apply


def test_render_source_placeholder_svg_contains_source_wordmark():
    svg = render_source_placeholder_svg(source="InfoQ", category="technology")

    assert svg.startswith("<?xml")
    assert "<svg" in svg
    # Wordmark uses uppercase source name.
    assert ">INFOQ<" in svg
    # Category badge rendered.
    assert "TECHNOLOGY" in svg
    # Gradient def for background is present.
    assert 'id="bg"' in svg


def test_render_source_placeholder_svg_escapes_unsafe_source():
    svg = render_source_placeholder_svg(source="Evil<>&", category=None)

    # Raw <,>,& from source must not appear inside the rendered text element.
    # (They can still appear as part of SVG syntax itself.)
    assert "Evil<>&" not in svg
    # HTML-escaped forms are present.
    assert "EVIL&lt;&gt;&amp;" in svg


def test_render_source_placeholder_svg_is_deterministic():
    a = render_source_placeholder_svg(source="The Verge", category="technology")
    b = render_source_placeholder_svg(source="The Verge", category="technology")

    assert a == b


def test_render_source_placeholder_png_is_mobile_renderable():
    png = render_source_placeholder_png(source="InfoQ", category="technology")

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 1000


def test_build_source_placeholder_url_relative_when_base_url_empty(_with_settings):
    _with_settings(base_url="")

    url = build_source_placeholder_url(source="InfoQ", category="AI")

    assert url.startswith(f"/api/v1{PLACEHOLDER_URL_MARKER}?")
    assert "source=InfoQ" in url
    assert "category=ai" in url


def test_build_source_placeholder_url_absolute_when_base_url_present(_with_settings):
    _with_settings(base_url="https://api.example.com")

    url = build_source_placeholder_url(source="Ars Technica")

    assert url.startswith("https://api.example.com/api/v1/placeholder/source?")
    assert "source=Ars%20Technica" in url


def test_build_source_placeholder_url_explicit_base_url_wins_over_settings(_with_settings):
    _with_settings(base_url="https://wrong.example.com")

    url = build_source_placeholder_url(
        source="InfoQ",
        category="technology",
        base_url="https://right.example.com/",
    )

    assert url.startswith("https://right.example.com/api/v1/placeholder/source?")


def test_is_placeholder_image_url_detects_marker():
    assert is_placeholder_image_url(
        "https://api.example.com/api/v1/placeholder/source?source=InfoQ"
    )
    assert is_placeholder_image_url("/api/v1/placeholder/source?source=TheVerge")


def test_is_placeholder_image_url_rejects_unrelated_urls():
    assert not is_placeholder_image_url(None)
    assert not is_placeholder_image_url("")
    assert not is_placeholder_image_url("https://cdn.example.com/hero.jpg")


def test_should_apply_placeholder_requires_feature_flag(_with_settings):
    _with_settings(enabled=False)

    item = SimpleNamespace(
        image_url=None,
        article_image_checked_at=datetime.utcnow() - timedelta(hours=2),
    )
    assert should_apply_placeholder(item) is False


def test_should_apply_placeholder_requires_prior_attempt(_with_settings):
    _with_settings(enabled=True, min_age_minutes=30)

    item = SimpleNamespace(image_url=None, article_image_checked_at=None)
    assert should_apply_placeholder(item) is False


def test_should_apply_placeholder_enforces_min_age(_with_settings):
    _with_settings(enabled=True, min_age_minutes=30)

    now = datetime.utcnow()
    # 10 minutes < 30 minute threshold -> not yet eligible.
    too_recent = SimpleNamespace(
        image_url=None,
        article_image_checked_at=now - timedelta(minutes=10),
    )
    assert should_apply_placeholder(too_recent, now=now) is False

    # 45 minutes > 30 minute threshold -> eligible.
    due = SimpleNamespace(
        image_url=None,
        article_image_checked_at=now - timedelta(minutes=45),
    )
    assert should_apply_placeholder(due, now=now) is True


def test_should_apply_placeholder_respects_min_age_override(_with_settings):
    _with_settings(enabled=True, min_age_minutes=30)

    now = datetime.utcnow()
    item = SimpleNamespace(
        image_url=None,
        article_image_checked_at=now - timedelta(minutes=10),
    )

    # Caller overrides threshold to 5 minutes -> eligible despite settings.
    assert should_apply_placeholder(item, now=now, min_age_minutes=5) is True


def test_should_apply_placeholder_skips_when_non_placeholder_image_already_set(
    _with_settings,
):
    _with_settings(enabled=True, min_age_minutes=30)

    now = datetime.utcnow()
    item = SimpleNamespace(
        image_url="https://cdn.example.com/hero.jpg",
        article_image_checked_at=now - timedelta(hours=2),
    )
    assert should_apply_placeholder(item, now=now) is False


def test_should_apply_placeholder_allows_replacing_existing_placeholder(_with_settings):
    _with_settings(enabled=True, min_age_minutes=30)

    now = datetime.utcnow()
    item = SimpleNamespace(
        image_url=f"/api/v1{PLACEHOLDER_URL_MARKER}?source=stale",
        article_image_checked_at=now - timedelta(hours=2),
    )
    # Placeholder URLs shouldn't block re-apply; the gate only protects real images.
    assert should_apply_placeholder(item, now=now) is True


def test_apply_source_placeholder_writes_url(_with_settings):
    _with_settings(enabled=True, base_url="https://api.example.com")

    item = SimpleNamespace(source="InfoQ", topics=["technology"], image_url=None)
    assert apply_source_placeholder(item) is True
    assert item.image_url is not None
    assert PLACEHOLDER_URL_MARKER in item.image_url
    assert "source=InfoQ" in item.image_url
    assert "category=technology" in item.image_url


def test_apply_source_placeholder_is_noop_when_feature_disabled(_with_settings):
    _with_settings(enabled=False)

    item = SimpleNamespace(source="InfoQ", topics=[], image_url=None)
    assert apply_source_placeholder(item) is False
    assert item.image_url is None


def test_apply_source_placeholder_handles_missing_topics(_with_settings):
    _with_settings(enabled=True)

    item = SimpleNamespace(source="InfoQ", topics=None, image_url=None)
    assert apply_source_placeholder(item) is True
    assert item.image_url is not None
    # No category query param when topics is missing.
    assert "category=" not in item.image_url
