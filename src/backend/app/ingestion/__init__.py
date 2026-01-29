"""Ingestion module.

Keep imports light here.

This package is imported in unit tests and tooling contexts where we don't want to
eagerly import optional/heavy integration dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ingestion.extractors import extract_entities, extract_source, extract_topics

if TYPE_CHECKING:  # pragma: no cover
    from app.ingestion.service import IngestionPipeline

__all__ = [
    "IngestionPipeline",
    "create_ingestion_pipeline",
    "extract_topics",
    "extract_entities",
    "extract_source",
]


def __getattr__(name: str):
    if name == "IngestionPipeline":
        from app.ingestion.service import IngestionPipeline as _IngestionPipeline

        return _IngestionPipeline
    if name == "create_ingestion_pipeline":
        from app.ingestion.service import create_ingestion_pipeline as _create

        return _create

    raise AttributeError(name)
