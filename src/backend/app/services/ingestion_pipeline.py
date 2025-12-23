"""
Content ingestion pipeline for the curation system.

Transforms existing articles and videos into unified content_items,
handling:
- Normalization and deduplication
- Metadata extraction (topics, entities)
- Initial scoring
- Clustering for related stories

Works alongside existing article/video fetching, converting
items after they're processed by summarizers.
"""

import hashlib
import re
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.config import get_settings
from app.models.article import Article
from app.models.video import Video
from app.models.content import ContentItem, ContentType
from app.repositories.content_repo import ContentItemRepository
from app.repositories.article_repo import ArticleRepository
from app.services.clustering_service import ClusteringService, compute_dedupe_key
from app.services.scoring_service import ScoringService, get_source_quality_weight

logger = get_logger(__name__)
settings = get_settings()


# Common tech topics for extraction
TECH_TOPICS = {
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "llm", "gpt", "chatgpt", "claude", "openai", "anthropic", "google",
    "apple", "microsoft", "meta", "amazon", "nvidia", "tesla",
    "iphone", "android", "ios", "macos", "windows", "linux",
    "cybersecurity", "security", "privacy", "hacking", "breach",
    "blockchain", "crypto", "bitcoin", "ethereum", "web3", "nft",
    "startup", "vc", "funding", "acquisition", "ipo",
    "robotics", "automation", "autonomous", "self-driving",
    "ar", "vr", "metaverse", "mixed reality", "vision pro",
    "5g", "6g", "networking", "cloud", "aws", "azure", "gcp",
    "quantum", "computing", "semiconductor", "chip", "cpu", "gpu",
    "software", "saas", "api", "developer", "programming",
    "smartphone", "laptop", "wearable", "smartwatch", "tablet",
    "gaming", "esports", "console", "playstation", "xbox", "nintendo",
    "streaming", "netflix", "spotify", "youtube", "tiktok",
    "social media", "twitter", "instagram", "linkedin", "reddit",
    "ecommerce", "fintech", "edtech", "healthtech", "biotech",
    "space", "spacex", "nasa", "satellite", "rocket",
}

# Named entities (companies, products, people)
TECH_ENTITIES = {
    # Companies
    "apple", "google", "microsoft", "meta", "amazon", "nvidia", "tesla",
    "openai", "anthropic", "deepmind", "intel", "amd", "qualcomm",
    "samsung", "sony", "lg", "huawei", "xiaomi", "bytedance",
    "netflix", "spotify", "uber", "airbnb", "stripe", "coinbase",
    "salesforce", "adobe", "oracle", "ibm", "cisco", "vmware",
    "snap", "pinterest", "twitter", "reddit", "discord",
    
    # Products
    "iphone", "ipad", "mac", "macbook", "airpods", "apple watch", "vision pro",
    "pixel", "android", "chrome", "chromebook", "gmail", "youtube",
    "windows", "xbox", "surface", "copilot", "bing", "teams",
    "playstation", "switch", "oculus", "quest",
    "chatgpt", "gpt-4", "gpt-5", "claude", "gemini", "llama", "mistral",
    "dall-e", "midjourney", "stable diffusion", "sora",
    
    # People
    "elon musk", "tim cook", "satya nadella", "mark zuckerberg", "sundar pichai",
    "sam altman", "jensen huang", "jeff bezos", "bill gates", "steve jobs",
    "dario amodei", "ilya sutskever",
}


class IngestionPipeline:
    """
    Pipeline for ingesting content into the curation system.
    
    Converts articles and videos into unified content_items,
    extracts metadata, and triggers clustering/scoring.
    """
    
    def __init__(
        self,
        db: Session,
        content_repo: ContentItemRepository,
        article_repo: ArticleRepository,
        clustering_service: ClusteringService,
        scoring_service: ScoringService
    ):
        self.db = db
        self.content_repo = content_repo
        self.article_repo = article_repo
        self.clustering = clustering_service
        self.scoring = scoring_service
    
    def ingest_article(self, article: Article) -> Optional[ContentItem]:
        """
        Ingest an article into the content_items table.
        
        Args:
            article: Article model instance
            
        Returns:
            Created ContentItem or None if duplicate
        """
        # Check for existing content item
        dedupe_key = compute_dedupe_key(article.title, self._extract_source(article.source_url))
        existing = self.content_repo.get_by_dedupe_key(dedupe_key)
        
        if existing:
            logger.debug(f"Article already ingested: {article.title}")
            return None
        
        # Extract metadata
        topics = self._extract_topics(article.title, article.summary or "")
        entities = self._extract_entities(article.title, article.summary or "")
        
        # Create content item
        content_item = ContentItem(
            type=ContentType.ARTICLE,
            source=self._extract_source(article.source_url),
            source_url=article.source_url,
            published_at=article.published_date or datetime.utcnow(),
            title=article.title,
            description=article.content[:500] if article.content else None,
            summary=article.summary,
            image_url=article.image_url,
            video_url=None,
            duration=None,
            topics=topics,
            entities=entities,
            raw_data={"article_id": article.id},
            dedupe_key=dedupe_key
        )
        
        # Initial quality score
        content_item.quality_score = get_source_quality_weight(content_item.source)
        content_item.recency_score = 1.0  # Fresh content
        
        # Save to database
        self.db.add(content_item)
        self.db.commit()
        self.db.refresh(content_item)
        
        # Run clustering
        self.clustering.cluster_new_item(content_item)
        
        # Compute full scores
        scores = self.scoring.score_single_item(content_item)
        self.content_repo.update_scores(
            content_item.id,
            quality_score=scores["quality"],
            trend_score=scores["trend"],
            recency_score=scores["recency"],
            diversity_boost=scores["diversity"],
            global_score=scores["global"]
        )
        
        logger.info(f"Ingested article: {article.title} -> ContentItem {content_item.id}")
        return content_item
    
    def ingest_video(self, video: Video, content_type: ContentType = ContentType.VIDEO) -> Optional[ContentItem]:
        """
        Ingest a video into the content_items table.
        
        Args:
            video: Video model instance
            content_type: VIDEO or REEL (based on duration)
            
        Returns:
            Created ContentItem or None if duplicate
        """
        # Determine type based on duration if not specified
        if content_type == ContentType.VIDEO and video.duration:
            # Reels are < 60 seconds
            if video.duration < 60:
                content_type = ContentType.REEL
        
        # Extract source from channel name
        source = video.channel_name or self._extract_source_from_youtube_url(video.youtube_url)
        
        # Check for existing content item
        dedupe_key = compute_dedupe_key(video.title, source)
        existing = self.content_repo.get_by_dedupe_key(dedupe_key)
        
        if existing:
            logger.debug(f"Video already ingested: {video.title}")
            return None
        
        # Extract metadata
        topics = self._extract_topics(video.title, video.summary or video.description or "")
        entities = self._extract_entities(video.title, video.summary or video.description or "")
        
        # Create content item
        content_item = ContentItem(
            type=content_type,
            source=source,
            source_url=video.youtube_url,
            published_at=video.published_at or datetime.utcnow(),
            title=video.title,
            description=video.description[:500] if video.description else None,
            summary=video.summary if content_type != ContentType.REEL else None,
            image_url=video.thumbnail_url,
            video_url=video.youtube_url,
            duration=video.duration,
            topics=topics,
            entities=entities,
            raw_data={"video_id": video.id, "youtube_id": video.youtube_id},
            dedupe_key=dedupe_key
        )
        
        # Initial quality score
        content_item.quality_score = get_source_quality_weight(content_item.source)
        content_item.recency_score = 1.0
        
        # Save to database
        self.db.add(content_item)
        self.db.commit()
        self.db.refresh(content_item)
        
        # Run clustering (mainly for articles, videos less likely to cluster)
        self.clustering.cluster_new_item(content_item)
        
        # Compute full scores
        scores = self.scoring.score_single_item(content_item)
        self.content_repo.update_scores(
            content_item.id,
            quality_score=scores["quality"],
            trend_score=scores["trend"],
            recency_score=scores["recency"],
            diversity_boost=scores["diversity"],
            global_score=scores["global"]
        )
        
        logger.info(f"Ingested video: {video.title} -> ContentItem {content_item.id}")
        return content_item
    
    def run_backfill(
        self,
        hours_back: int = 72,
        limit: int = 500
    ) -> Dict[str, int]:
        """
        Backfill existing articles and videos into content_items.
        
        Args:
            hours_back: How far back to look
            limit: Maximum items per type
            
        Returns:
            Statistics about the backfill
        """
        stats = {
            "articles_processed": 0,
            "articles_ingested": 0,
            "videos_processed": 0,
            "videos_ingested": 0,
            "errors": 0
        }
        
        # Get recent articles
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        articles = self.article_repo.get_recent(since=cutoff, limit=limit)
        
        logger.info(f"Backfilling {len(articles)} articles")
        
        for article in articles:
            try:
                stats["articles_processed"] += 1
                result = self.ingest_article(article)
                if result:
                    stats["articles_ingested"] += 1
            except Exception as e:
                logger.error(f"Error ingesting article {article.id}: {e}")
                stats["errors"] += 1
        
        # Get recent videos (need to import VideoRepository)
        try:
            from app.repositories.video_repo import VideoRepository
            video_repo = VideoRepository(self.db)
            videos = video_repo.get_recent(since=cutoff, limit=limit)
            
            logger.info(f"Backfilling {len(videos)} videos")
            
            for video in videos:
                try:
                    stats["videos_processed"] += 1
                    result = self.ingest_video(video)
                    if result:
                        stats["videos_ingested"] += 1
                except Exception as e:
                    logger.error(f"Error ingesting video {video.id}: {e}")
                    stats["errors"] += 1
        except Exception as e:
            logger.error(f"Error backfilling videos: {e}")
            stats["errors"] += 1
        
        logger.info(f"Backfill complete: {stats}")
        return stats
    
    def _extract_topics(self, title: str, content: str) -> List[str]:
        """Extract topics from title and content."""
        text = f"{title} {content}".lower()
        found_topics = []
        
        for topic in TECH_TOPICS:
            # Match whole words
            pattern = r'\b' + re.escape(topic) + r'\b'
            if re.search(pattern, text):
                found_topics.append(topic)
        
        # Limit to top 5 topics
        return found_topics[:5]
    
    def _extract_entities(self, title: str, content: str) -> List[str]:
        """Extract named entities from title and content."""
        text = f"{title} {content}".lower()
        found_entities = []
        
        for entity in TECH_ENTITIES:
            # Match whole words
            pattern = r'\b' + re.escape(entity) + r'\b'
            if re.search(pattern, text):
                found_entities.append(entity)
        
        # Limit to top 5 entities
        return found_entities[:5]
    
    def _extract_source(self, url: str) -> str:
        """Extract source name from URL."""
        if not url:
            return "unknown"
        
        # Domain mapping
        domain_to_source = {
            "techcrunch.com": "TechCrunch",
            "theverge.com": "The Verge",
            "wired.com": "Wired",
            "arstechnica.com": "Ars Technica",
            "engadget.com": "Engadget",
            "mashable.com": "Mashable",
            "cnet.com": "CNET",
            "zdnet.com": "ZDNet",
            "venturebeat.com": "VentureBeat",
            "technologyreview.com": "MIT Technology Review",
            "spectrum.ieee.org": "IEEE Spectrum",
            "9to5mac.com": "9to5Mac",
            "9to5google.com": "9to5Google",
            "macrumors.com": "MacRumors",
            "androidcentral.com": "Android Central",
            "tomsguide.com": "Tom's Guide",
            "tomshardware.com": "Tom's Hardware",
            "anandtech.com": "AnandTech",
        }
        
        for domain, source in domain_to_source.items():
            if domain in url.lower():
                return source
        
        # Extract domain as fallback
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace("www.", "")
            return domain.split(".")[0].title()
        except:
            return "Unknown"
    
    def _extract_source_from_youtube_url(self, url: str) -> str:
        """Extract channel info from YouTube URL."""
        if not url:
            return "YouTube"
        return "YouTube"


def create_ingestion_pipeline(db: Session) -> IngestionPipeline:
    """Factory function to create IngestionPipeline with dependencies."""
    from app.repositories.content_repo import ContentItemRepository
    from app.repositories.article_repo import ArticleRepository
    from app.repositories.user_repo import InteractionEventRepository
    from app.services.clustering_service import ClusteringService
    from app.services.scoring_service import ScoringService
    
    content_repo = ContentItemRepository(db)
    article_repo = ArticleRepository(db)
    event_repo = InteractionEventRepository(db)
    
    clustering = ClusteringService(content_repo)
    scoring = ScoringService(content_repo, event_repo)
    
    return IngestionPipeline(
        db=db,
        content_repo=content_repo,
        article_repo=article_repo,
        clustering_service=clustering,
        scoring_service=scoring
    )
