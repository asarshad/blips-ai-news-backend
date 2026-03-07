"""
Service layer for business logic.

Services coordinate between repositories, integrations, and provide
business logic for the application.

NOTE: Scoring, Clustering, and Ingestion have been moved to dedicated modules:
- app.ranking - Scoring functionality
- app.clustering - Clustering functionality
- app.ingestion - Ingestion pipeline (direct RSS/YouTube fetching)
"""

from app.clustering import ClusteringService
from app.ingestion import IngestionPipeline, create_ingestion_pipeline

# Re-export from new locations for backward compatibility
from app.ranking import ScoringService
from app.services.ai_chat import AiChatService
from app.services.multi_factor_ranking_service import MultiFactorRankingService
from app.services.quota_manager import QuotaManager
from app.services.source_quality_service import SourceQualityService
from app.services.tiered_feed_service import get_tiered_feed, invalidate_tiered_feed_cache

__all__ = [
    "AiChatService",
    "QuotaManager",
    "MultiFactorRankingService",
    "SourceQualityService",
    # Re-exports
    "ScoringService",
    "ClusteringService",
    "IngestionPipeline",
    "create_ingestion_pipeline",
    "get_tiered_feed",
    "invalidate_tiered_feed_cache",
]
