
from app.core.logging import get_logger
from app.models.article import Article
from app.repositories.article_repo import ArticleRepository
from app.integrations.openai_client import OpenAIClient
from typing import Dict, Any, Optional

logger = get_logger(__name__)

class ArticleSummarizer:
    def __init__(
        self, 
        article_repo: ArticleRepository,
        openai_client: Optional[OpenAIClient] = None
    ):
        self.article_repo = article_repo
        self.openai_client = openai_client or OpenAIClient()
    
    def summarize_article(self, article_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate summary and tags for an article using GPT"""
        try:
            title = article_data.get("title", "")
            content = article_data.get("content", "")
            
            # Use OpenAI client for summarization
            result = self.openai_client.summarize_article(title, content)
            
            return {
                "title": title,
                "source_url": article_data.get("source_url", ""),
                "content": content,
                "summary": result.summary,
                "image_url": article_data.get("image_url", ""),
                "tags": result.tags
            }
            
        except Exception as e:
            logger.error(f"Error summarizing article: {str(e)}")
            
            # Return original data with placeholder summary if API fails
            return {
                "title": article_data.get("title", ""),
                "source_url": article_data.get("source_url", ""),
                "content": article_data.get("content", ""),
                "summary": "Summary unavailable at the moment.",
                "image_url": article_data.get("image_url", ""),
                "tags": []
            }
    
    def regenerate_summaries(self, limit: int = 10) -> int:
        """Regenerate summaries for articles that have placeholder summaries"""
        try:
            # Find articles with placeholder summaries
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
                    if processed["summary"] and processed["summary"] != "Summary unavailable at the moment.":
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
            
        except Exception as e:
            logger.error(f"Error in regenerate_summaries: {str(e)}")
            return 0
    
    def save_article(self, article_data: Dict[str, Any]) -> Article:
        """Save processed article to database"""
        try:
            return self.article_repo.create_with_tags(article_data)
        except Exception as e:
            logger.error(f"Error saving article: {str(e)}")
            raise
