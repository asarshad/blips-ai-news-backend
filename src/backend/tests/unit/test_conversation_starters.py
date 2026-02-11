"""Unit tests for conversation starters service."""

import json

import pytest

from app.models.content import ContentType
from app.services.conversation_starters import (
    DEFAULT_FALLBACK_STARTERS,
    ConversationStartersService,
    StarterGenerationError,
    get_starter_prompt,
    parse_starters_response,
)


class TestGetStarterPrompt:
    """Tests for prompt generation."""
    
    def test_article_prompt_contains_title(self):
        """Prompt should contain the article title."""
        prompt = get_starter_prompt(
            title="AI Revolution in Tech",
            summary="AI is changing everything.",
            content_type=ContentType.ARTICLE
        )
        
        assert "AI Revolution in Tech" in prompt
        assert "article" in prompt.lower()
    
    def test_video_prompt_contains_video_label(self):
        """Video content should use video label in prompt."""
        prompt = get_starter_prompt(
            title="Understanding ML",
            summary="Machine learning basics.",
            content_type=ContentType.VIDEO
        )
        
        assert "video" in prompt.lower()
    
    def test_prompt_requests_json_format(self):
        """Prompt should request JSON output format."""
        prompt = get_starter_prompt(
            title="Test",
            summary="Test summary",
            content_type=ContentType.ARTICLE
        )
        
        assert "JSON" in prompt
        assert '"starters"' in prompt
        assert '"fallback"' in prompt


class TestParseStartersResponse:
    """Tests for response parsing."""
    
    def test_parse_valid_json(self):
        """Should parse valid JSON response."""
        response = json.dumps({
            "starters": ["Q1?", "Q2?", "Q3?"],
            "fallback": ["F1?", "F2?"]
        })
        
        result = parse_starters_response(response)
        
        assert len(result["starters"]) == 3
        assert len(result["fallback"]) == 2
        assert result["starters"][0] == "Q1?"
    
    def test_parse_json_with_markdown_wrapper(self):
        """Should handle JSON wrapped in markdown code blocks."""
        response = """```json
{
    "starters": ["Question 1?", "Question 2?"],
    "fallback": ["Fallback?"]
}
```"""
        
        result = parse_starters_response(response)
        
        assert len(result["starters"]) == 2
        assert result["starters"][0] == "Question 1?"
    
    def test_parse_limits_starters_to_five(self):
        """Should limit starters to 5 max."""
        response = json.dumps({
            "starters": ["Q1?", "Q2?", "Q3?", "Q4?", "Q5?", "Q6?", "Q7?"],
            "fallback": ["F?"]
        })
        
        result = parse_starters_response(response)
        
        assert len(result["starters"]) == 5
    
    def test_parse_uses_default_fallback_if_empty(self):
        """Should use default fallback if response has empty fallback."""
        response = json.dumps({
            "starters": ["Q1?"],
            "fallback": []
        })
        
        result = parse_starters_response(response)
        
        assert result["fallback"] == DEFAULT_FALLBACK_STARTERS
    
    def test_parse_invalid_json_raises_error(self):
        """Should raise error for invalid JSON."""
        with pytest.raises(StarterGenerationError):
            parse_starters_response("not valid json")
    
    def test_parse_empty_starters_raises_error(self):
        """Should raise error if no starters in response."""
        response = json.dumps({
            "starters": [],
            "fallback": ["F?"]
        })
        
        with pytest.raises(StarterGenerationError):
            parse_starters_response(response)


class TestConversationStartersServiceWithFakeLLM:
    """Tests using FakeLLM provider."""
    
    @pytest.fixture
    def fake_llm_client(self):
        """Create LLM client with fake provider."""
        from app.integrations.llm_client import LLMClient
        return LLMClient(provider="fake")
    
    @pytest.fixture
    def service(self, fake_llm_client):
        """Create starters service with fake LLM."""
        return ConversationStartersService(llm_client=fake_llm_client)
    
    @pytest.fixture
    def mock_article(self):
        """Create mock article content item."""
        from unittest.mock import MagicMock
        
        item = MagicMock()
        item.id = 1
        item.title = "AI Breakthrough: New Model Achieves Human-Level Performance"
        item.summary = "Researchers have developed a new AI model."
        item.description = "Full description here."
        item.type = ContentType.ARTICLE
        item.conversation_starters = None
        return item
    
    @pytest.fixture
    def mock_video(self):
        """Create mock video content item."""
        from unittest.mock import MagicMock
        
        item = MagicMock()
        item.id = 2
        item.title = "Understanding Machine Learning Basics"
        item.summary = "A tutorial on ML fundamentals."
        item.description = None
        item.type = ContentType.VIDEO
        item.conversation_starters = None
        return item
    
    def test_generate_starters_for_article(self, service, mock_article):
        """Should generate starters for article."""
        result = service.generate_starters(mock_article)
        
        assert "starters" in result
        assert "fallback" in result
        assert len(result["starters"]) >= 1
        assert len(result["fallback"]) >= 1
    
    def test_generate_starters_for_video(self, service, mock_video):
        """Should generate starters for video."""
        result = service.generate_starters(mock_video)
        
        assert "starters" in result
        assert len(result["starters"]) >= 1
    
    def test_returns_cached_starters_if_present(self, service, mock_article):
        """Should return cached starters without calling LLM."""
        mock_article.conversation_starters = {
            "starters": ["Cached Q1?"],
            "fallback": ["Cached F1?"]
        }
        
        result = service.generate_starters(mock_article)
        
        assert result["starters"] == ["Cached Q1?"]
    
    def test_force_regenerate_ignores_cache(self, service, mock_article, fake_llm_client):
        """Force regenerate should ignore cached starters."""
        mock_article.conversation_starters = {
            "starters": ["Old cached Q?"],
            "fallback": ["Old F?"]
        }
        
        result = service.generate_starters(mock_article, force_regenerate=True)
        
        # FakeLLM generates new starters
        assert result["starters"] != ["Old cached Q?"]
    
    def test_generate_and_persist_updates_item(self, service, mock_article):
        """generate_and_persist should update the content item."""
        result = service.generate_and_persist(mock_article)
        
        assert mock_article.conversation_starters == result
        assert "starters" in mock_article.conversation_starters


class TestFakeLLMClient:
    """Tests for the FakeLLM implementation."""
    
    def test_fake_llm_is_always_configured(self):
        """FakeLLM should always report as configured."""
        from app.integrations.fake_llm import FakeLLMClient
        
        client = FakeLLMClient()
        
        assert client.is_configured() is True
    
    def test_fake_llm_returns_deterministic_response(self):
        """FakeLLM should return consistent responses."""
        from app.integrations.fake_llm import FakeLLMClient
        from app.integrations.llm_client import ChatMessage
        
        client = FakeLLMClient()
        
        msg = ChatMessage(role="user", content="Generate conversation starters for title: Test")
        response1 = client.chat([msg])
        
        client.reset()
        response2 = client.chat([msg])
        
        assert response1.content == response2.content
    
    def test_fake_llm_tracks_call_count(self):
        """FakeLLM should track number of calls."""
        from app.integrations.fake_llm import FakeLLMClient
        from app.integrations.llm_client import ChatMessage
        
        client = FakeLLMClient()
        
        assert client.get_call_count() == 0
        
        client.chat([ChatMessage(role="user", content="Hello")])
        assert client.get_call_count() == 1
        
        client.chat([ChatMessage(role="user", content="World")])
        assert client.get_call_count() == 2
        
        client.reset()
        assert client.get_call_count() == 0
    
    def test_fake_llm_detects_starters_request(self):
        """FakeLLM should detect and respond to starter generation requests."""
        from app.integrations.fake_llm import FakeLLMClient
        from app.integrations.llm_client import ChatMessage
        
        client = FakeLLMClient()
        
        msg = ChatMessage(
            role="user",
            content="Generate conversation starters for: Test Article Title"
        )
        response = client.chat([msg])
        
        # Should return JSON with starters
        data = json.loads(response.content)
        assert "starters" in data
        assert "fallback" in data
