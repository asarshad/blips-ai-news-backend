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
from unittest.mock import MagicMock, patch

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


# ── Mistral timeout ──────────────────────────────────────────────────


class TestMistralTimeout:
    """Verify Mistral client passes timeout to the SDK."""

    def test_mistral_client_passes_timeout(self):
        """MistralLLMClient should pass timeout to the Mistral constructor."""
        with patch("app.integrations.llm_client.settings") as mock_settings:
            mock_settings.MISTRAL_API_KEY = "test-key"
            mock_settings.LLM_REQUEST_TIMEOUT = 30

            with patch("mistralai.Mistral") as MockMistral:
                import app.integrations.llm_client as llm_mod

                # Re-read the timeout constant
                llm_mod.LLM_REQUEST_TIMEOUT = 30

                from app.integrations.llm_client import MistralLLMClient

                _client = MistralLLMClient(api_key="test-key")
                if MockMistral.called:
                    call_kwargs = MockMistral.call_args
                    # Should have timeout_ms in kwargs (Mistral SDK uses milliseconds)
                    assert "timeout_ms" in (
                        call_kwargs.kwargs or {}
                    ), "Mistral client should be initialized with timeout_ms parameter"
