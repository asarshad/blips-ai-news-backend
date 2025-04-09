
import openai
from app.config import settings
from app.models.article import Article
from app.models.conversation import Conversation
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import logging

openai.api_key = settings.OPENAI_API_KEY
logger = logging.getLogger(__name__)

class AiChatService:
    def __init__(self, db: Session):
        self.db = db
    
    def get_ai_response(self, article_id: int, user_message: str, history_limit: int = 3) -> Dict[str, Any]:
        """Generate AI response to user message with article context"""
        try:
            # Get article
            article = self.db.query(Article).filter(Article.id == article_id).first()
            if not article:
                return {"error": "Article not found"}
            
            # Get recent conversation history
            history = self.db.query(Conversation).filter(
                Conversation.article_id == article_id
            ).order_by(Conversation.timestamp.desc()).limit(history_limit).all()
            
            # Reverse to get chronological order
            history = history[::-1]
            
            # Build messages for OpenAI
            messages = [
                {
                    "role": "system", 
                    "content": f"""You are an AI assistant that discusses tech news articles with users.
                    You are knowledgeable, helpful, and focused on the article topic.
                    
                    Article Title: {article.title}
                    Article Summary: {article.summary}
                    
                    Keep responses concise (max 3 paragraphs) and directly relevant to the article.
                    If asked about topics unrelated to the article, politely redirect to the article topic.
                    """
                }
            ]
            
            # Add conversation history
            for msg in history:
                role = "user" if msg.sender == "user" else "assistant"
                messages.append({"role": role, "content": msg.message})
            
            # Add current user message
            messages.append({"role": "user", "content": user_message})
            
            # Call OpenAI API
            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=300,
                temperature=0.7
            )
            
            ai_response = response.choices[0].message.content
            usage = response.usage.total_tokens if response.usage else 0
            
            return {
                "response": ai_response,
                "tokens_used": usage
            }
            
        except Exception as e:
            logger.error(f"Error generating AI response: {str(e)}")
            return {
                "response": "I'm sorry, I'm having trouble processing your request right now. Please try again later.",
                "tokens_used": 0
            }
    
    def save_conversation(self, article_id: int, user_message: str, ai_response: str) -> Dict[str, Any]:
        """Save user message and AI response to conversation history"""
        try:
            # Save user message
            user_conv = Conversation(
                article_id=article_id,
                message=user_message,
                sender="user"
            )
            
            # Save AI response
            ai_conv = Conversation(
                article_id=article_id,
                message=ai_response,
                sender="ai"
            )
            
            self.db.add(user_conv)
            self.db.add(ai_conv)
            self.db.commit()
            
            return {
                "user_id": user_conv.id,
                "ai_id": ai_conv.id
            }
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving conversation: {str(e)}")
            raise
