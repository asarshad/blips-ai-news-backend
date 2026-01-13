
from app.core.logging import get_logger
from app.core.exceptions import SummarizationError
from app.models.article import Article
from app.repositories.article_repo import ArticleRepository
from app.integrations.llm_client import LLMClient
from typing import Dict, Any, Optional

logger = get_logger(__name__)


class ArticleSummarizer:
    """Service for generating article summaries and tags using AI."""
    
    def __init__(
        self, 
        article_repo: ArticleRepository,
        llm_client: Optional[LLMClient] = None
    ):
        self.article_repo = article_repo
        self.llm_client = llm_client or LLMClient()
    
    def summarize_article(self, article_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate summary and tags for an article using LLM.
        
        Args:
            article_data: Dict with 'title', 'content', 'source_url', 'image_url'
            
        Returns:
            Processed article data with 'summary' and 'tags' added
        """
        title = article_data.get("title", "")
        content = article_data.get("content", "")
        
        try:
            result = self.llm_client.summarize_article(title, content)
            
            return {
                "title": title,
                "source_url": article_data.get("source_url", ""),
                "content": content,
                "summary": result.summary,
                "image_url": article_data.get("image_url", ""),
                "tags": result.tags
            }
            
        except Exception as e:
            logger.error(f"Error summarizing article '{title}': {str(e)}")
            # Return with placeholder summary - allows article to be saved
            return {
                "title": title,
                "source_url": article_data.get("source_url", ""),
                "content": content,
                "summary": "",
                "image_url": article_data.get("image_url", ""),
                "tags": []
            }
    
    def regenerate_summaries(self, limit: int = 10) -> int:
        """
        Regenerate summaries for articles that have placeholder summaries.
        
        Args:
            limit: Maximum number of articles to process
            
        Returns:
            Number of articles successfully updated
        """
        articles = self.article_repo.get_with_placeholder_summary(limit)
        updated_count = 0
        
        for article in articles:
            try:
                article_data = {
                    "title": article.title,
                    "content": article.content or "",
                    "source_url": article.source_url,
                    "image_url": article.image_url
                }
                
                processed = self.summarize_article(article_data)
                
                # Only update if we got a real summary
                if processed["summary"] != "":
                    self.article_repo.update_summary_and_tags(
                        article.id,
                        processed["summary"],
                        processed.get("tags", [])
                    )
                    updated_count += 1
                    logger.info(f"Updated summary for: {article.title[:50]}")
                    
            except Exception as e:
                logger.error(f"Error regenerating summary for article {article.id}: {str(e)}")
                continue
        
        return updated_count
    
    def save_article(self, article_data: Dict[str, Any]) -> Article:
        """
        Save processed article to database.
        
        Args:
            article_data: Processed article data including summary and tags
            
        Returns:
            Created Article model instance
        """
        return self.article_repo.create_with_tags(article_data)
