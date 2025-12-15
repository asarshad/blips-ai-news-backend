
import openai
from app.core.config import settings
from app.core.logging import get_logger
from app.models.article import Article
from app.models.conversation import Conversation
from app.repositories.article_repo import ArticleRepository
from app.repositories.conversation_repo import ConversationRepository
from typing import Dict, Any

openai.api_key = settings.OPENAI_API_KEY
logger = get_logger(__name__)

class AiChatService:
    def __init__(
        self, 
        article_repo: ArticleRepository, 
        conversation_repo: ConversationRepository
    ):
        self.article_repo = article_repo
        self.conversation_repo = conversation_repo
    
    def get_ai_response(self, article_id: int, user_message: str, history_limit: int = 3) -> Dict[str, Any]:
        """Generate AI response to user message with article context"""
        try:
            # Get article
            article = self.article_repo.get_by_id(article_id)
            if not article:
                return {"error": "Article not found"}
            
            # Get recent conversation history
            history = self.conversation_repo.get_recent_by_article(article_id, history_limit)
            
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
            user_conv = self.conversation_repo.add_user_message(article_id, user_message)
            ai_conv = self.conversation_repo.add_ai_message(article_id, ai_response)
            
            return {
                "user_id": user_conv.id,
                "ai_id": ai_conv.id
            }
            
        except Exception as e:
            logger.error(f"Error saving conversation: {str(e)}")
            raise
