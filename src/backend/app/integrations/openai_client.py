"""
OpenAI integration client.

Provides a clean interface for interacting with OpenAI's API,
abstracting away API-specific details from business logic.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import openai

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChatMessage:
    """Represents a message in a chat conversation."""
    role: str  # "system", "user", or "assistant"
    content: str


@dataclass
class ChatResponse:
    """Response from OpenAI chat completion."""
    content: str
    tokens_used: int
    model: str


@dataclass
class SummaryResult:
    """Result from article summarization."""
    summary: str
    tags: List[str]


class OpenAIClient:
    """
    Client for OpenAI API interactions.
    
    Centralizes all OpenAI API calls and provides a clean interface
    for the rest of the application.
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        """
        Initialize OpenAI client.
        
        Args:
            api_key: OpenAI API key. Defaults to settings.OPENAI_API_KEY
            model: Model to use for completions. Defaults to gpt-4o-mini
        
        Raises:
            ValueError: If API key is missing or empty
        """
        self.api_key = api_key or settings.OPENAI_API_KEY
        
        if not self.api_key or self.api_key.strip() == "" or self.api_key == "your-openai-api-key-here":
            logger.warning("OpenAI API key is missing or invalid. AI features will be unavailable.")
            self.api_key = None
        
        self.model = model
        if self.api_key:
            openai.api_key = self.api_key
    
    def is_configured(self) -> bool:
        """Check if OpenAI client is properly configured with a valid API key."""
        return self.api_key is not None and self.api_key.strip() != ""
    
    def chat(
        self, 
        messages: List[ChatMessage], 
        max_tokens: int = 300,
        temperature: float = 0.7
    ) -> ChatResponse:
        """
        Send a chat completion request to OpenAI.
        
        Args:
            messages: List of ChatMessage objects
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0-2)
            
        Returns:
            ChatResponse with content and usage info
            
        Raises:
            Exception: If API key is not configured or API call fails
        """
        if not self.is_configured():
            raise Exception("OpenAI API key is not configured. Please set OPENAI_API_KEY environment variable.")
        
        try:
            api_messages = [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]
            
            response = openai.chat.completions.create(
                model=self.model,
                messages=api_messages,
                max_tokens=max_tokens,
                temperature=temperature
            )
            
            return ChatResponse(
                content=response.choices[0].message.content,
                tokens_used=response.usage.total_tokens if response.usage else 0,
                model=self.model
            )
            
        except Exception as e:
            logger.error(f"OpenAI chat error: {str(e)}")
            raise
    
    def summarize_article(
        self, 
        title: str, 
        content: str,
        max_content_length: int = 4000
    ) -> SummaryResult:
        """
        Generate a summary and tags for an article.
        
        Args:
            title: Article title
            content: Article content
            max_content_length: Max chars of content to send
            
        Returns:
            SummaryResult with summary and tags
            
        Raises:
            Exception: If API key is not configured or summarization fails
        """
        if not self.is_configured():
            raise Exception("OpenAI API key is not configured")
        
        try:
            truncated_content = content[:max_content_length]
            
            prompt = f"""
            Article Title: {title}
            
            Article Content: {truncated_content}
            
            Task 1: Write a concise summary of this tech article in exactly 85-90 words. Keep it informative and engaging.
            
            Task 2: Generate 5-7 relevant tags for this article, separated by commas.
            
            Format your response as:
            SUMMARY: [your summary here]
            TAGS: [tag1, tag2, tag3, etc.]
            """
            
            messages = [
                ChatMessage(
                    role="system",
                    content="You are a tech journalist assistant that creates concise, informative summaries of tech news articles."
                ),
                ChatMessage(role="user", content=prompt)
            ]
            
            response = self.chat(messages, max_tokens=500)
            
            # Parse response
            summary = ""
            tags = []
            
            for line in response.content.split('\n'):
                if line.startswith('SUMMARY:'):
                    summary = line[8:].strip()
                elif line.startswith('TAGS:'):
                    tags_text = line[5:].strip()
                    tags = [tag.strip() for tag in tags_text.split(',')]
            
            if not summary:
                raise Exception("Failed to parse summary from OpenAI response")
            
            return SummaryResult(summary=summary, tags=tags)
            
        except Exception as e:
            logger.error(f"Article summarization error: {str(e)}")
            raise

    def summarize_video(
        self, 
        title: str, 
        description: str,
        max_length: int = 4000
    ) -> str:
        """
        Generate a summary for a video based on its description.
        
        Args:
            title: Video title
            description: Video description
            max_length: Max chars of description to send
            
        Returns:
            Summary string
            
        Raises:
            Exception: If API key is not configured or summarization fails
        """
        if not self.is_configured():
            raise Exception("OpenAI API key is not configured")
        
        try:
            truncated_desc = description[:max_length]
            
            prompt = f"""
            Video Title: {title}
            
            Video Description: {truncated_desc}
            
            Task: Write a concise summary of this video in exactly 85-90 words based on the description. 
            Focus on the main topic and key points. Remove any channel promotion, "link in bio", or "subscribe" text.
            
            Format your response as just the summary text.
            """
            
            messages = [
                ChatMessage(
                    role="system",
                    content="You are a tech journalist assistant that creates concise, informative summaries of tech videos."
                ),
                ChatMessage(role="user", content=prompt)
            ]
            
            response = self.chat(messages, max_tokens=200)
            summary = response.content.strip()
            
            if not summary:
                raise Exception("Empty summary returned from OpenAI")
            
            return summary
            
        except Exception as e:
            logger.error(f"Video summarization error: {str(e)}")
            raise
    
    def generate_chat_response(
        self,
        article_title: str,
        article_summary: str,
        conversation_history: List[Dict[str, str]],
        user_message: str
    ) -> ChatResponse:
        """
        Generate an AI response for article chat.
        
        Args:
            article_title: Title of the article being discussed
            article_summary: Summary of the article
            conversation_history: Previous messages in the conversation
            user_message: The user's current message
            
        Returns:
            ChatResponse with AI's response
        """
        system_prompt = f"""You are an AI assistant that discusses tech news articles with users.
You are knowledgeable, helpful, and focused on the article topic.

Article Title: {article_title}
Article Summary: {article_summary}

Keep responses concise (max 3 paragraphs) and directly relevant to the article.
If asked about topics unrelated to the article, politely redirect to the article topic.
"""
        
        messages = [ChatMessage(role="system", content=system_prompt)]
        
        # Add conversation history
        for msg in conversation_history:
            role = "user" if msg.get("sender") == "user" else "assistant"
            messages.append(ChatMessage(role=role, content=msg.get("message", "")))
        
        # Add current user message
        messages.append(ChatMessage(role="user", content=user_message))
        
        return self.chat(messages, max_tokens=300, temperature=0.7)
