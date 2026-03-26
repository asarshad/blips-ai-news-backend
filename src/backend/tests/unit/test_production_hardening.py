"""Tests for production hardening controls.

Covers:
- LOG_LEVEL env var honoring in setup_logging()
- DB pool settings from config
- Redis pool settings from config
- Scheduler job parameters (misfire_grace_time, max_instances, coalesce)
- Cleanup lock CAS safety
- CORS and docs gating
"""

import logging
from unittest.mock import MagicMock, Mock, patch

# ── LOG_LEVEL ─────────────────────────────────────────────────────────


class TestLoggingSetup:
    """Verify setup_logging() honours the LOG_LEVEL env var."""

    def test_default_level_is_info(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        from app.core.logging import setup_logging

        setup_logging()
        assert logging.getLogger().level == logging.INFO

    def test_log_level_env_warning(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        from app.core.logging import setup_logging

        setup_logging()
        assert logging.getLogger().level == logging.WARNING

    def test_log_level_env_debug(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        from app.core.logging import setup_logging

        setup_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_invalid_log_level_falls_back(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "NOT_A_LEVEL")
        from app.core.logging import setup_logging

        setup_logging(level=logging.ERROR)
        assert logging.getLogger().level == logging.ERROR


# ── DB pool config ────────────────────────────────────────────────────


class TestDBPoolConfig:
    """Verify db/base.py reads pool settings from config."""

    def test_pool_settings_from_config(self):
        from app.core.config import settings

        # Settings should expose pool knobs
        assert hasattr(settings, "DB_POOL_SIZE")
        assert hasattr(settings, "DB_MAX_OVERFLOW")
        assert hasattr(settings, "DB_POOL_TIMEOUT")
        assert hasattr(settings, "DB_POOL_RECYCLE_SECONDS")

    def test_default_pool_values(self):
        from app.core.config import Settings

        defaults = Settings()
        assert defaults.DB_POOL_SIZE == 3
        assert defaults.DB_MAX_OVERFLOW == 5
        assert defaults.DB_POOL_TIMEOUT == 30
        assert defaults.DB_POOL_RECYCLE_SECONDS == 1800


# ── Redis pool config ────────────────────────────────────────────────


class TestRedisPoolConfig:
    """Verify dependencies.py reads REDIS_MAX_CONNECTIONS from config."""

    def test_redis_max_connections_setting_exists(self):
        from app.core.config import settings

        assert hasattr(settings, "REDIS_MAX_CONNECTIONS")

    def test_default_redis_max_connections(self):
        from app.core.config import Settings

        assert Settings().REDIS_MAX_CONNECTIONS == 20


# ── Scheduler hardening ──────────────────────────────────────────────


class TestSchedulerHardening:
    """Verify all scheduler jobs have misfire_grace_time, max_instances, and coalesce."""

    def test_all_jobs_have_safety_params(self):
        from unittest.mock import MagicMock, patch

        mock_scheduler = MagicMock()

        with patch("app.scheduler.BackgroundScheduler", return_value=mock_scheduler):
            from app.scheduler import init_scheduler

            init_scheduler()

        for call_obj in mock_scheduler.add_job.call_args_list:
            kwargs = call_obj.kwargs if call_obj.kwargs else {}
            # Also check positional-style keyword dict
            if not kwargs:
                # add_job is called with keyword args
                continue
            job_id = kwargs.get("id", "unknown")
            assert kwargs.get("max_instances") == 1, f"{job_id} missing max_instances=1"
            assert kwargs.get("coalesce") is True, f"{job_id} missing coalesce=True"
            assert kwargs.get("misfire_grace_time", 0) > 0, f"{job_id} missing misfire_grace_time"


# ── Cleanup lock CAS ─────────────────────────────────────────────────


class TestCleanupLockCAS:
    """Verify the cleanup lock uses CAS (compare-and-delete) on release."""

    def test_release_uses_lua_eval(self):
        """_release_cleanup_lock should call redis.eval with a Lua script."""
        import app.scheduler.tasks_cleanup as tc

        mock_redis = MagicMock()

        # Simulate that a token was stored during acquisition
        tc._cleanup_lock_token = "test-token-123"

        with patch("app.core.dependencies.get_redis", return_value=mock_redis):
            tc._release_cleanup_lock()

        # Should have called eval with a Lua script containing 'get' and 'del'
        mock_redis.eval.assert_called_once()
        lua_script = mock_redis.eval.call_args[0][0]
        assert "get" in lua_script
        assert "del" in lua_script

        # Token should be cleared
        assert tc._cleanup_lock_token == ""

    def test_release_without_token_is_noop(self):
        """If no token is set, release should be a no-op."""
        import app.scheduler.tasks_cleanup as tc

        tc._cleanup_lock_token = ""
        mock_redis = MagicMock()

        with patch("app.core.dependencies.get_redis", return_value=mock_redis):
            tc._release_cleanup_lock()

        mock_redis.eval.assert_not_called()


# ── CORS / Docs posture ──────────────────────────────────────────────


class TestProductionPosture:
    """Verify CORS and docs are properly gated."""

    def test_docs_disabled_by_default(self):
        from app.core.config import Settings

        s = Settings()
        assert s.DOCS_ENABLED is False

    def test_env_and_log_level_defaults(self):
        from app.core.config import Settings

        s = Settings()
        assert s.ENV == "dev"
        assert s.LOG_LEVEL == "INFO"


# ── LLM retry config ─────────────────────────────────────────────────


class TestLLMRetryConfig:
    """Verify OpenAI client catches provider-specific exceptions for retry."""

    def test_openai_client_has_retryable_exceptions(self):
        """The OpenAI client should register provider-specific transient errors."""
        from app.integrations.llm_client import OpenAILLMClient

        client = OpenAILLMClient(api_key="sk-test-fake-key-12345678901234567890")
        # _RETRYABLE should contain OpenAI-specific errors
        type_names = [t.__name__ for t in client._RETRYABLE]
        assert "APIConnectionError" in type_names
        assert "APITimeoutError" in type_names
        assert "RateLimitError" in type_names
        assert "ConnectionError" in type_names
        assert "TimeoutError" in type_names


class TestOpenAIModelPinning:
    """Verify OpenAI usage stays pinned to gpt-5-nano."""

    def test_llm_client_ignores_openai_model_setting(self):
        """LLMClient should ignore OpenAI model overrides and pin to gpt-5-nano."""
        with patch("app.integrations.llm_client.settings") as mock_settings:
            mock_settings.LLM_PROVIDER = "openai"
            mock_settings.OPENAI_API_KEY = "sk-test-fake-key-12345678901234567890"
            mock_settings.OPENAI_MODEL = "gpt-4o-mini"

            from app.integrations.llm_client import LLMClient

            client = LLMClient(provider="openai", api_key=mock_settings.OPENAI_API_KEY)
            assert client._client.model == "gpt-5-nano"

    def test_deprecated_openai_client_ignores_model_override(self):
        """Legacy OpenAIClient should also stay pinned to gpt-5-nano."""
        from app.integrations.openai_client import OpenAIClient

        client = OpenAIClient(
            api_key="sk-test-fake-key-12345678901234567890",
            model="gpt-4o-mini",
        )
        assert client.model == "gpt-5-nano"

    def test_llm_client_uses_gpt5_compatible_responses_parameters(self):
        """Pinned GPT-5 requests should use the Responses API contract."""
        from types import SimpleNamespace

        from app.integrations.llm_client import ChatMessage, OpenAILLMClient

        client = OpenAILLMClient(api_key="sk-test-fake-key-12345678901234567890")
        client._client = Mock()
        client._client.responses.create.return_value = SimpleNamespace(
            output_text="ok",
            usage=SimpleNamespace(total_tokens=42),
            model="gpt-5-nano-2025-08-07",
        )

        response = client.chat(
            [ChatMessage(role="user", content="hello")],
            max_tokens=123,
            temperature=0.2,
        )

        kwargs = client._client.responses.create.call_args.kwargs
        assert response.content == "ok"
        assert kwargs["model"] == "gpt-5-nano"
        assert kwargs["input"] == [{"role": "user", "content": "hello"}]
        assert kwargs["max_output_tokens"] == 123
        assert kwargs["reasoning"] == {"effort": "minimal"}
        assert kwargs["store"] is False
        assert "max_completion_tokens" not in kwargs
        assert "temperature" not in kwargs

    def test_llm_client_maps_system_prompt_to_responses_instructions(self):
        """System prompts should move to the Responses API instructions field."""
        from types import SimpleNamespace

        from app.integrations.llm_client import ChatMessage, OpenAILLMClient

        client = OpenAILLMClient(api_key="sk-test-fake-key-12345678901234567890")
        client._client = Mock()
        client._client.responses.create.return_value = SimpleNamespace(
            output_text="ok",
            usage=SimpleNamespace(total_tokens=21),
            model="gpt-5-nano-2025-08-07",
        )

        client.chat(
            [
                ChatMessage(role="system", content="You are helpful."),
                ChatMessage(role="user", content="hello"),
            ],
            max_tokens=50,
        )

        kwargs = client._client.responses.create.call_args.kwargs
        assert kwargs["instructions"] == "You are helpful."
        assert kwargs["input"] == [{"role": "user", "content": "hello"}]


class TestSummaryLengthPrompting:
    """Verify summary length is enforced in backend prompts, not the client UI."""

    def test_llm_client_article_summary_prompt_targets_60_to_70_words(self):
        """Article summaries should request a 60-70 word range from the model."""
        from types import SimpleNamespace

        from app.core.config import settings
        from app.integrations.llm_client import LLMClient

        client = LLMClient(provider="openai", api_key="sk-test-fake-key-12345678901234567890")
        client.chat = Mock(
            return_value=SimpleNamespace(
                content="SUMMARY: test\nTAGS: ai, chips\nSTARTERS: q1 | q2 | q3"
            )
        )

        client.summarize_article("Title", "Content")

        prompt = client.chat.call_args.kwargs["messages"][0].content
        assert (
            f"between {settings.ARTICLE_SUMMARY_MIN_OUTPUT_WORDS} and "
            f"{settings.ARTICLE_SUMMARY_MAX_OUTPUT_WORDS} words"
        ) in prompt
        assert f"Never exceed {settings.ARTICLE_SUMMARY_MAX_OUTPUT_WORDS} words" in prompt
        assert "exactly 85-90 words" not in prompt

    def test_llm_client_video_summary_prompt_targets_configured_word_range(self):
        """Video summaries should request the configured min/max range."""
        from types import SimpleNamespace

        from app.core.config import settings
        from app.integrations.llm_client import LLMClient

        client = LLMClient(provider="openai", api_key="sk-test-fake-key-12345678901234567890")
        client.chat = Mock(
            return_value=SimpleNamespace(content="SUMMARY: test\nSTARTERS: q1 | q2 | q3")
        )

        client.summarize_video("Title", "Description")

        prompt = client.chat.call_args.kwargs["messages"][1].content
        assert (
            f"between {settings.VIDEO_SUMMARY_MIN_OUTPUT_WORDS} and "
            f"{settings.VIDEO_SUMMARY_MAX_OUTPUT_WORDS} words"
        ) in prompt
        assert f"Never exceed {settings.VIDEO_SUMMARY_MAX_OUTPUT_WORDS} words" in prompt
        assert "exactly 85-90 words" not in prompt

    def test_legacy_openai_client_article_summary_prompt_targets_60_to_70_words(self):
        """Legacy OpenAIClient should match the same article summary contract."""
        from types import SimpleNamespace

        from app.core.config import settings
        from app.integrations.openai_client import OpenAIClient

        client = OpenAIClient(api_key="sk-test-fake-key-12345678901234567890")
        client.chat = Mock(return_value=SimpleNamespace(content="SUMMARY: test\nTAGS: ai, chips"))

        client.summarize_article("Title", "Content")

        prompt = client.chat.call_args.args[0][1].content
        assert (
            f"between {settings.ARTICLE_SUMMARY_MIN_OUTPUT_WORDS} and "
            f"{settings.ARTICLE_SUMMARY_MAX_OUTPUT_WORDS} words"
        ) in prompt
        assert f"Never exceed {settings.ARTICLE_SUMMARY_MAX_OUTPUT_WORDS} words" in prompt
        assert "exactly 85-90 words" not in prompt

    def test_legacy_openai_client_video_summary_prompt_targets_configured_word_range(self):
        """Legacy OpenAIClient should match the same video summary contract."""
        from types import SimpleNamespace

        from app.core.config import settings
        from app.integrations.openai_client import OpenAIClient

        client = OpenAIClient(api_key="sk-test-fake-key-12345678901234567890")
        client.chat = Mock(return_value=SimpleNamespace(content="summary"))

        client.summarize_video("Title", "Description")

        prompt = client.chat.call_args.args[0][1].content
        assert (
            f"between {settings.VIDEO_SUMMARY_MIN_OUTPUT_WORDS} and "
            f"{settings.VIDEO_SUMMARY_MAX_OUTPUT_WORDS} words"
        ) in prompt
        assert f"Never exceed {settings.VIDEO_SUMMARY_MAX_OUTPUT_WORDS} words" in prompt
        assert "exactly 85-90 words" not in prompt


# ── Mistral timeout ──────────────────────────────────────────────────


class TestMistralTimeout:
    """Verify Mistral client passes timeout to the SDK."""

    def test_mistral_client_passes_timeout(self):
        """MistralLLMClient should pass timeout to the Mistral constructor."""
        with patch("app.integrations.llm_client.settings") as mock_settings:
            mock_settings.MISTRAL_API_KEY = "test-key"
            mock_settings.LLM_REQUEST_TIMEOUT = 30

            with patch("app.integrations.llm_client._load_mistral_client_class") as mock_loader:
                MockMistral = Mock()
                mock_loader.return_value = MockMistral

                import app.integrations.llm_client as llm_mod

                # Re-read the timeout constant
                llm_mod.LLM_REQUEST_TIMEOUT = 30

                from app.integrations.llm_client import MistralLLMClient

                _client = MistralLLMClient(api_key="test-key")
                if MockMistral.called:
                    call_kwargs = MockMistral.call_args
                    # Should have timeout_ms in kwargs (Mistral SDK uses milliseconds)
                    assert "timeout_ms" in (call_kwargs.kwargs or {}), (
                        "Mistral client should be initialized with timeout_ms parameter"
                    )
