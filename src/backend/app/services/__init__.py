"""
Service layer for business logic.

Services coordinate between repositories, integrations, and provide
business logic for the application.

NOTE: Scoring, Clustering, and Ingestion have been moved to dedicated modules:
- app.ranking - Scoring functionality
- app.clustering - Clustering functionality
- app.ingestion - Ingestion pipeline (direct RSS/YouTube fetching)
"""

from importlib import import_module

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


_LAZY_EXPORTS = {
    "AiChatService": ("app.services.ai_chat", "AiChatService"),
    "QuotaManager": ("app.services.quota_manager", "QuotaManager"),
    "MultiFactorRankingService": (
        "app.services.multi_factor_ranking_service",
        "MultiFactorRankingService",
    ),
    "SourceQualityService": ("app.services.source_quality_service", "SourceQualityService"),
    "ScoringService": ("app.ranking", "ScoringService"),
    "ClusteringService": ("app.clustering", "ClusteringService"),
    "IngestionPipeline": ("app.ingestion", "IngestionPipeline"),
    "create_ingestion_pipeline": ("app.ingestion", "create_ingestion_pipeline"),
    "get_tiered_feed": ("app.services.tiered_feed_service", "get_tiered_feed"),
    "invalidate_tiered_feed_cache": (
        "app.services.tiered_feed_service",
        "invalidate_tiered_feed_cache",
    ),
}


def __getattr__(name):
    """Lazily resolve imports that would otherwise create package cycles."""
    target = _LAZY_EXPORTS.get(name)
    if target is not None:
        module_name, attr_name = target
        value = getattr(import_module(module_name), attr_name)
        globals()[name] = value
        return value
    try:
        module = import_module(f"{__name__}.{name}")
    except ModuleNotFoundError:
        module = None
    if module is not None:
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
