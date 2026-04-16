import types
from types import SimpleNamespace
from unittest.mock import Mock, patch


def test_load_mistral_client_class_uses_top_level_module(monkeypatch):
    from app.integrations.llm_client import _load_mistral_client_class

    fake_pkg = types.ModuleType("mistralai")
    fake_client = object()
    fake_pkg.Mistral = fake_client

    monkeypatch.setitem(__import__("sys").modules, "mistralai", fake_pkg)

    assert _load_mistral_client_class() is fake_client


def test_load_mistral_client_class_falls_back_to_client_submodule(monkeypatch):
    from app.integrations.llm_client import _load_mistral_client_class

    fake_pkg = types.ModuleType("mistralai")
    fake_pkg.__path__ = []
    fake_submodule = types.ModuleType("mistralai.client")
    fake_client = object()
    fake_submodule.Mistral = fake_client

    monkeypatch.setitem(__import__("sys").modules, "mistralai", fake_pkg)
    monkeypatch.setitem(__import__("sys").modules, "mistralai.client", fake_submodule)

    assert _load_mistral_client_class() is fake_client


def test_mistral_client_initializes_with_timeout_ms():
    with patch("app.integrations.llm_client.settings") as mock_settings:
        mock_settings.MISTRAL_API_KEY = "test-key"
        mock_settings.LLM_REQUEST_TIMEOUT = 30

        with patch("app.integrations.llm_client._load_mistral_client_class") as mock_loader:
            mock_constructor = Mock()
            mock_loader.return_value = mock_constructor

            import app.integrations.llm_client as llm_mod

            llm_mod.LLM_REQUEST_TIMEOUT = 30

            from app.integrations.llm_client import MistralLLMClient

            MistralLLMClient(api_key="test-key")

            mock_constructor.assert_called_once()
            assert mock_constructor.call_args.kwargs["timeout_ms"] == 30000


def test_extract_article_image_url_uses_strict_extract_only_prompt():
    from app.integrations.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="sk-test-fake-key-12345678901234567890")
    client.chat = Mock(return_value=SimpleNamespace(content="IMAGE_URL: /images/hero.jpg"))

    image_url = client.extract_article_image_url(
        article_url="https://example.com/story",
        title="Story",
        document='MEDIA: <img src="/images/hero.jpg" alt="Hero" />',
    )

    prompt = client.chat.call_args.kwargs["messages"][1].content
    assert image_url == "/images/hero.jpg"
    assert "Do not invent, guess, search the web, or rewrite a URL." in prompt
    assert "Return only an image URL that is explicitly present in the provided document." in prompt


def test_extract_article_image_url_allows_logo_fallback_prompt_when_requested():
    from app.integrations.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="sk-test-fake-key-12345678901234567890")
    client.chat = Mock(return_value=SimpleNamespace(content="IMAGE_URL: /images/company-logo.png"))

    image_url = client.extract_article_image_url(
        article_url="https://example.com/story",
        title="Story",
        document='MEDIA: <img src="/images/company-logo.png" alt="Company logo" />',
        allow_logo_fallback=True,
    )

    prompt = client.chat.call_args.kwargs["messages"][1].content
    assert image_url == "/images/company-logo.png"
    assert "high-resolution company or product logo only as a last resort" in prompt
    assert "Prefer logos that are large, centered" in prompt


def test_summarize_video_parses_structured_classifier_payload():
    from app.integrations.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="sk-test-fake-key-12345678901234567890")
    client.chat = Mock(
        return_value=SimpleNamespace(
            content="""{
  "tech_relevance": "meaningful",
  "confidence": 0.82,
  "is_mixed_roundup": false,
  "reason": "Technology materially shapes why the story matters.",
  "summary": "This video explains how cloud tooling is helping cancer researchers speed up diagnostics while outlining the remaining clinical limits and tradeoffs in deploying those systems responsibly across hospitals.",
  "starters": [
    "How central is the cloud infrastructure angle here?",
    "What limits still exist for hospitals adopting this tech?",
    "Why does this matter beyond the medical example?"
  ]
}"""
        )
    )

    result = client.summarize_video(
        "How Google Cloud is helping cancer diagnostics",
        "A deeper look at the medical workflow and the supporting cloud systems.",
    )

    assert result.tech_relevance == "meaningful"
    assert result.tech_relevance_confidence == 0.82
    assert result.is_mixed_roundup is False
    assert result.tech_relevance_reason == "Technology materially shapes why the story matters."
    assert result.summary
    assert result.conversation_starters["starters"][0].startswith("How central")


def test_summarize_video_rejects_malformed_json_classifier_payload():
    import pytest

    from app.integrations.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="sk-test-fake-key-12345678901234567890")
    client.chat = Mock(
        return_value=SimpleNamespace(
            content='{"tech_relevance":"primary","confidence":0.66,"is_mixed_roundup":false,"reason":"Mainly about Macs","summary":"Broken'
        )
    )

    with pytest.raises(ValueError, match="Malformed JSON returned from LLM for video summary"):
        client.summarize_video("Apple's 2026 Macs have LEAKED!", "New Mac rumors and performance claims")


def test_openai_chat_passes_previous_response_id_to_responses_api():
    from app.integrations.llm_client import ChatMessage, OpenAILLMClient

    client = OpenAILLMClient(api_key="sk-test-fake-key-12345678901234567890")
    responses_api = Mock()
    responses_api.create.return_value = SimpleNamespace(
        output_text="follow-up",
        usage=SimpleNamespace(total_tokens=12),
        model="gpt-5-nano",
        id="resp_new",
    )
    client._client = SimpleNamespace(responses=responses_api)

    response = client.chat(
        [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="What else?"),
        ],
        previous_response_id="resp_prev",
        store=True,
    )

    assert response.response_id == "resp_new"
    assert responses_api.create.call_args.kwargs["previous_response_id"] == "resp_prev"
    assert responses_api.create.call_args.kwargs["store"] is True


def test_generate_chat_response_prefers_previous_response_id_for_openai_followups():
    from app.integrations.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="sk-test-fake-key-12345678901234567890")
    client.chat = Mock(
        return_value=SimpleNamespace(
            content="follow-up",
            tokens_used=8,
            response_id="resp_next",
        )
    )

    response = client.generate_chat_response(
        article_title="Test",
        article_summary="Summary",
        conversation_history=[
            {"sender": "user", "message": "Tell me more"},
            {"sender": "ai", "message": "Here are some details."},
        ],
        user_message="What else?",
        previous_response_id="resp_prev",
    )

    messages = client.chat.call_args.args[0]
    assert response.response_id == "resp_next"
    assert [message.role for message in messages] == ["system", "user"]
    assert client.chat.call_args.kwargs["previous_response_id"] == "resp_prev"
    assert client.chat.call_args.kwargs["store"] is True


def test_generate_chat_response_uses_legacy_history_when_response_id_missing():
    from app.integrations.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="sk-test-fake-key-12345678901234567890")
    client.chat = Mock(
        return_value=SimpleNamespace(
            content="legacy-follow-up",
            tokens_used=9,
            response_id=None,
        )
    )

    client.generate_chat_response(
        article_title="Test",
        article_summary="Summary",
        conversation_history=[
            {"sender": "user", "message": "Tell me more"},
            {"sender": "ai", "message": "Here are some details."},
        ],
        user_message="What else?",
    )

    messages = client.chat.call_args.args[0]
    assert [message.role for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "previous_response_id" not in client.chat.call_args.kwargs
    assert client.chat.call_args.kwargs["store"] is True


def test_generate_chat_response_falls_back_when_previous_response_id_is_missing():
    from app.integrations.llm_client import LLMClient

    class FakePreviousResponseNotFound(Exception):
        def __init__(self):
            super().__init__("previous response missing")
            self.body = {
                "error": {
                    "code": "previous_response_not_found",
                }
            }

    client = LLMClient(provider="openai", api_key="sk-test-fake-key-12345678901234567890")
    client.chat = Mock(
        side_effect=[
            FakePreviousResponseNotFound(),
            SimpleNamespace(
                content="fallback-follow-up",
                tokens_used=11,
                response_id="resp_replayed",
            ),
        ]
    )

    response = client.generate_chat_response(
        article_title="Test",
        article_summary="Summary",
        conversation_history=[
            {"sender": "user", "message": "Tell me more"},
            {"sender": "ai", "message": "Here are some details."},
        ],
        user_message="What else?",
        previous_response_id="resp_prev",
    )

    first_call = client.chat.call_args_list[0]
    second_call = client.chat.call_args_list[1]

    assert response.response_id == "resp_replayed"
    assert first_call.kwargs["previous_response_id"] == "resp_prev"
    assert [message.role for message in second_call.args[0]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "previous_response_id" not in second_call.kwargs
