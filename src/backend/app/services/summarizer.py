
import openai
from app.config import settings
from app.models.article import Article, Tag
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import logging

openai.api_key = settings.OPENAI_API_KEY
logger = logging.getLogger(__name__)

class ArticleSummarizer:
    def __init__(self, db: Session):
        self.db = db
    
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
    
    def save_article(self, article_data: Dict[str, Any]) -> Article:
        """Save processed article to database"""
        try:
            # Create new article
            article = Article(
                title=article_data["title"],
                source_url=article_data["source_url"],
                content=article_data["content"],
                summary=article_data["summary"],
                image_url=article_data["image_url"]
            )
            
            self.db.add(article)
            self.db.commit()
            self.db.refresh(article)
            
            # Add tags
            for tag_name in article_data["tags"]:
                # Check if tag exists
                tag = self.db.query(Tag).filter(Tag.name == tag_name).first()
                if not tag:
                    tag = Tag(name=tag_name)
                    self.db.add(tag)
                
                article.tags.append(tag)
            
            self.db.commit()
            return article
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving article: {str(e)}")
            raise
