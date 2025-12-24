"""
Ingestion module.

Provides content ingestion functionality for articles and videos.
"""

from app.ingestion.extractors import extract_topics, extract_entities, extract_source
from app.ingestion.service import IngestionPipeline, create_ingestion_pipeline

__all__ = [
    "IngestionPipeline",
    "create_ingestion_pipeline",
    "extract_topics",
    "extract_entities",
    "extract_source",
]
