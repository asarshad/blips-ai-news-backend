import types
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
    from types import SimpleNamespace

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
