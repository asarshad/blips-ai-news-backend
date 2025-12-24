"""
Content ingestion pipeline for the curation system.

DEPRECATED: This module has been refactored into app.ingestion

The functionality has been split into:
- app.ingestion.extractors - Topic/entity extraction functions
- app.ingestion.service - IngestionPipeline class

This file remains for backward compatibility. Import from app.ingestion instead.
"""

# Re-export from new location for backward compatibility
from app.ingestion import (
    IngestionPipeline,
    create_ingestion_pipeline,
    extract_topics,
    extract_entities,
    extract_source,
)

# Re-export TECH_TOPICS and TECH_ENTITIES for backward compatibility
from app.config.content import TECH_TOPICS, TECH_ENTITIES

__all__ = [
    "IngestionPipeline",
    "create_ingestion_pipeline",
    "extract_topics",
    "extract_entities",
    "extract_source",
    "TECH_TOPICS",
    "TECH_ENTITIES",
]
