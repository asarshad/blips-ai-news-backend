"""
Service layer for business logic.

Services coordinate between repositories, integrations, and provide
business logic for the application.

NOTE: Scoring, Clustering, and Ingestion have been moved to dedicated modules:
- app.ranking - Scoring functionality
- app.clustering - Clustering functionality  
- app.ingestion - Ingestion pipeline
"""

from app.services.article_service import ArticleService
from app.services.video_service import VideoService
from app.services.news_fetcher import NewsFetcher
from app.services.video_fetcher import VideoFetcher
from app.services.summarizer import ArticleSummarizer
from app.services.ai_chat import AiChatService
from app.services.quota_manager import QuotaManager

# Re-export from new locations for backward compatibility
from app.ranking import ScoringService
from app.clustering import ClusteringService
from app.ingestion import IngestionPipeline, create_ingestion_pipeline

__all__ = [
    "ArticleService",
    "VideoService", 
    "NewsFetcher",
    "VideoFetcher",
    "ArticleSummarizer",
    "AiChatService",
    "QuotaManager",
    # Re-exports
    "ScoringService",
    "ClusteringService",
    "IngestionPipeline",
    "create_ingestion_pipeline",
]
