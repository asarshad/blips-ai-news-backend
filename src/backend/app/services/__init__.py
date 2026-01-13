"""
Service layer for business logic.

Services coordinate between repositories, integrations, and provide
business logic for the application.

NOTE: Scoring, Clustering, and Ingestion have been moved to dedicated modules:
- app.ranking - Scoring functionality
- app.clustering - Clustering functionality  
- app.ingestion - Ingestion pipeline (direct RSS/YouTube fetching)
"""

from app.services.ai_chat import AiChatService
from app.services.quota_manager import QuotaManager

# Re-export from new locations for backward compatibility
from app.ranking import ScoringService
from app.clustering import ClusteringService
from app.ingestion import IngestionPipeline, create_ingestion_pipeline

__all__ = [
    "AiChatService",
    "QuotaManager",
    # Re-exports
    "ScoringService",
    "ClusteringService",
    "IngestionPipeline",
    "create_ingestion_pipeline",
]
