
import openai
from app.core.config import settings
from app.core.logging import get_logger
from app.models.article import Article
from app.repositories.article_repo import ArticleRepository
from typing import Dict, Any

openai.api_key = settings.OPENAI_API_KEY
logger = get_logger(__name__)

class ArticleSummarizer:
    def __init__(self, article_repo: ArticleRepository):
        self.article_repo = article_repo
    
    def summarize_article(self, article_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate summary and tags for an article using GPT"""
        try:
            # Create prompt for GPT
            title = article_data.get("title", "")
            content = article_data.get("content", "")
            
            prompt = f"""
            Article Title: {title}
            
            Article Content: {content[:4000]}
            
            Task 1: Write a concise summary of this tech article in 3-4 sentences.
            
            Task 2: Generate 5-7 relevant tags for this article, separated by commas.
            
            Format your response as:
            SUMMARY: [your summary here]
            TAGS: [tag1, tag2, tag3, etc.]
            """
            
            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a tech journalist assistant that creates concise, informative summaries of tech news articles."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500
            )
            
            # Extract summary and tags from response
            result = response.choices[0].message.content
            
            summary = ""
            tags = []
            
            for line in result.split('\n'):
                if line.startswith('SUMMARY:'):
                    summary = line[8:].strip()
                elif line.startswith('TAGS:'):
                    tags_text = line[5:].strip()
                    tags = [tag.strip() for tag in tags_text.split(',')]
            
            return {
                "title": title,
                "source_url": article_data.get("source_url", ""),
                "content": content,
                "summary": summary,
                "image_url": article_data.get("image_url", ""),
                "tags": tags
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
