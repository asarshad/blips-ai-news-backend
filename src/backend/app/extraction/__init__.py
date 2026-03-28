"""Content extraction module.

Provides robust article extraction with cascading fallbacks:
  1. trafilatura (primary main-text extractor)
  2. readability-lxml (fallback extractor)
  3. RSS-only excerpt (safe fallback)

Sub-modules:
  - fetcher:       httpx client with retries, rate limiting, caching headers
  - metadata:      canonical URL, og:image, twitter:image, title extraction
  - text_extract:  trafilatura + readability cascading text extraction
  - normalize:     URL normalization, text cleanup, validation helpers
  - pipeline:      orchestrates full extraction and returns ExtractionResult
"""

from __future__ import annotations

from typing import Any

__all__ = ["ExtractionResult", "ExtractionStatus", "ImageStatus", "run_extraction"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from app.extraction.pipeline import (
            ExtractionResult,
            ExtractionStatus,
            ImageStatus,
            run_extraction,
        )

        exports = {
            "ExtractionResult": ExtractionResult,
            "ExtractionStatus": ExtractionStatus,
            "ImageStatus": ImageStatus,
            "run_extraction": run_extraction,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
