"""Public endpoint for source-branded article placeholder images.

Returns a deterministic SVG rendered from the ``source`` and optional
``category`` query parameters. The route is intentionally unauthenticated
— placeholder URLs are embedded in article payloads and must be fetchable
by anonymous clients, share-card scrapers, and og-image consumers.

The response is cacheable for a long window because the output is a pure
function of the query parameters.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from app.services.article_image_placeholder import render_source_placeholder_svg

router = APIRouter()


@router.get("/placeholder/source", include_in_schema=False)
def get_source_placeholder(
    source: str = Query(..., max_length=120),
    category: str | None = Query(default=None, max_length=60),
    width: int = Query(default=1200, ge=240, le=2400),
    height: int = Query(default=630, ge=160, le=1600),
) -> Response:
    svg_body = render_source_placeholder_svg(
        source=source,
        category=category,
        width=width,
        height=height,
    )
    return Response(
        content=svg_body,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
