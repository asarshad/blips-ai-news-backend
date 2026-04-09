"""Tests for app.extraction.fetcher — SSRF protection, fetch logic, rate limiter."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from app.extraction.fetcher import (
    BROWSER_FALLBACK_USER_AGENT,
    _is_private_host,
    _rate_limit_domain,
    fetch_url,
)

# ═══════════════════════════════════════════════════════════════════════════════
# _is_private_host
# ═══════════════════════════════════════════════════════════════════════════════


class TestIsPrivateHost:
    """_is_private_host should detect all non-globally-routable addresses."""

    def test_loopback_ipv4(self):
        # 127.0.0.1 is loopback
        with patch("socket.getaddrinfo") as mock_ga:
            mock_ga.return_value = [(None, None, None, None, ("127.0.0.1", 0))]
            assert _is_private_host("localhost") is True

    def test_private_rfc1918_class_c(self):
        with patch("socket.getaddrinfo") as mock_ga:
            mock_ga.return_value = [(None, None, None, None, ("192.168.1.100", 0))]
            assert _is_private_host("internal.host") is True

    def test_link_local(self):
        with patch("socket.getaddrinfo") as mock_ga:
            mock_ga.return_value = [(None, None, None, None, ("169.254.169.254", 0))]
            assert _is_private_host("metadata.local") is True

    def test_public_ip_allowed(self):
        with patch("socket.getaddrinfo") as mock_ga:
            mock_ga.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
            assert _is_private_host("example.com") is False

    def test_dns_failure_returns_false(self):
        """If DNS resolution fails, we allow the request (fail will happen at connect)."""
        import socket as _socket

        with patch("socket.getaddrinfo", side_effect=_socket.gaierror("nxdomain")):
            assert _is_private_host("nonexistent.invalid") is False


# ═══════════════════════════════════════════════════════════════════════════════
# fetch_url — SSRF blocking
# ═══════════════════════════════════════════════════════════════════════════════


class TestFetchUrlSsrf:
    """fetch_url must reject private hosts before any HTTP connection is made."""

    def _mock_private(self, host_ip: str):
        """Patch getaddrinfo to resolve to a private IP."""
        return patch(
            "app.extraction.fetcher.socket.getaddrinfo",
            return_value=[(None, None, None, None, (host_ip, 0))],
        )

    def test_localhost_blocked(self):
        with self._mock_private("127.0.0.1"):
            result = fetch_url("http://localhost/secret")
        assert result.error is not None
        assert "SSRF" in result.error
        assert result.status_code == 0

    def test_private_ip_blocked(self):
        with self._mock_private("10.0.0.1"):
            result = fetch_url("http://10.0.0.1/data")
        assert result.error is not None
        assert "SSRF" in result.error

    def test_metadata_service_blocked(self):
        with self._mock_private("169.254.169.254"):
            result = fetch_url("http://169.254.169.254/latest/meta-data/")
        assert result.error is not None
        assert "SSRF" in result.error


# ═══════════════════════════════════════════════════════════════════════════════
# fetch_url — happy path
# ═══════════════════════════════════════════════════════════════════════════════


class TestFetchUrlHappyPath:
    """fetch_url should return populated FetchResult on HTTP 200."""

    def _mock_public_host(self):
        """Patch DNS to return a public IP."""
        return patch(
            "app.extraction.fetcher.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 0))],
        )

    def _mock_http_response(self, status: int, body: bytes = b"<html><body>Hi</body></html>"):
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.content = body
        mock_resp.text = body.decode("utf-8")
        mock_resp.encoding = "utf-8"
        mock_resp.headers = {"Content-Type": "text/html", "ETag": '"abc123"'}
        return mock_resp

    def test_200_returns_html(self):
        mock_resp = self._mock_http_response(200)
        with self._mock_public_host():
            with patch("app.extraction.fetcher._get_client", return_value=MagicMock()):
                with patch(
                    "app.extraction.fetcher._get_with_validated_redirects"
                ) as mock_httpx_get:
                    mock_httpx_get.return_value = (mock_resp, 10.0)
                    with patch("app.extraction.fetcher._rate_limit_domain"):
                        result = fetch_url("https://example.com/article")

        assert result.status_code == 200
        assert "Hi" in result.html
        assert result.etag == '"abc123"'
        assert result.error is None
        assert result.not_modified is False

    def test_304_not_modified(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 304
        mock_resp.headers = {"ETag": '"abc123"', "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT"}
        with self._mock_public_host():
            with patch("app.extraction.fetcher._get_client", return_value=MagicMock()):
                with patch(
                    "app.extraction.fetcher._get_with_validated_redirects"
                ) as mock_httpx_get:
                    mock_httpx_get.return_value = (mock_resp, 10.0)
                    with patch("app.extraction.fetcher._rate_limit_domain"):
                        result = fetch_url(
                            "https://example.com/article",
                            etag='"abc123"',
                        )

        assert result.status_code == 304
        assert result.not_modified is True
        assert result.error is None

    def test_image_response_returns_content_type_without_buffering_html(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "image/jpeg"}
        mock_resp.url = "https://cdn.example.com/hero.jpg"
        mock_resp.encoding = "utf-8"

        with self._mock_public_host():
            with patch("app.extraction.fetcher._get_client") as mock_client:
                mock_client.return_value = MagicMock()
                with patch(
                    "app.extraction.fetcher._get_with_validated_redirects"
                ) as mock_httpx_get:
                    mock_httpx_get.return_value = (mock_resp, 8.0)
                    with patch("app.extraction.fetcher._rate_limit_domain"):
                        result = fetch_url("https://cdn.example.com/hero.jpg")

        assert result.status_code == 200
        assert result.content_type == "image/jpeg"
        assert result.html == ""
        assert result.error is None

    def test_streaming_reader_caps_response_bytes(self):
        chunks = [b"a" * (2 * 1024 * 1024), b"b" * (4 * 1024 * 1024)]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_resp.url = "https://example.com/article"
        mock_resp.encoding = "utf-8"
        mock_resp.iter_bytes.return_value = iter(chunks)

        with self._mock_public_host():
            with patch("app.extraction.fetcher._get_client") as mock_client:
                mock_client.return_value = MagicMock()
                with patch(
                    "app.extraction.fetcher._get_with_validated_redirects"
                ) as mock_httpx_get:
                    mock_httpx_get.return_value = (mock_resp, 12.0)
                    with patch("app.extraction.fetcher._rate_limit_domain"):
                        result = fetch_url("https://example.com/article")

        assert result.status_code == 200
        assert len(result.html) == 5 * 1024 * 1024
        mock_resp.close.assert_called()

    def test_elapsed_always_defined_when_max_retries_zero(self):
        """elapsed_ms must be set even when max_retries=0 (loop body never runs)."""
        with self._mock_public_host():
            with patch("app.extraction.fetcher.get_settings") as mock_settings:
                settings = MagicMock()
                settings.EXTRACTION_MAX_RETRIES = 0
                settings.EXTRACTION_DOMAIN_MIN_INTERVAL = 0.0
                mock_settings.return_value = settings
                with patch("app.extraction.fetcher._rate_limit_domain"):
                    result = fetch_url("https://example.com/article")

        # Should not raise NameError; elapsed_ms defaults to 0.0
        assert isinstance(result.elapsed_ms, float)
        assert result.error is not None  # "All retries exhausted" or similar

    def test_redirect_to_private_host_is_blocked(self):
        first_response = MagicMock()
        first_response.status_code = 302
        first_response.headers = {"Location": "http://127.0.0.1/secret"}
        first_response.url = "https://example.com/article"

        with patch(
            "app.extraction.fetcher.socket.getaddrinfo",
            side_effect=[
                [(None, None, None, None, ("93.184.216.34", 0))],
                [(None, None, None, None, ("127.0.0.1", 0))],
            ],
        ):
            with patch("app.extraction.fetcher._get_client") as mock_client:
                client = MagicMock()
                client.build_request.return_value = object()
                client.send.return_value = first_response
                mock_client.return_value = client
                with patch("app.extraction.fetcher._rate_limit_domain"):
                    result = fetch_url("https://example.com/article")

        assert result.error is not None
        assert "SSRF" in result.error

    def test_redirect_to_public_host_is_followed(self):
        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {"Location": "https://cdn.example.com/final"}
        redirect_response.url = "https://example.com/article"

        final_response = self._mock_http_response(200, body=b"<html><body>Redirected</body></html>")
        final_response.url = "https://cdn.example.com/final"

        with patch(
            "app.extraction.fetcher.socket.getaddrinfo",
            side_effect=[
                [(None, None, None, None, ("93.184.216.34", 0))],
                [(None, None, None, None, ("93.184.216.35", 0))],
            ],
        ):
            with patch("app.extraction.fetcher._get_client") as mock_client:
                client = MagicMock()
                client.build_request.return_value = object()
                client.send.side_effect = [redirect_response, final_response]
                mock_client.return_value = client
                with patch("app.extraction.fetcher._rate_limit_domain"):
                    result = fetch_url("https://example.com/article")

        assert result.status_code == 200
        assert "Redirected" in result.html
        assert result.url == "https://cdn.example.com/final"

    def test_429_retries_once_with_browser_user_agent(self):
        blocked_response = MagicMock()
        blocked_response.status_code = 429
        blocked_response.headers = {}
        blocked_response.url = "https://example.com/article"

        recovered_response = self._mock_http_response(
            200, body=b"<html><body>Recovered</body></html>"
        )
        recovered_response.url = "https://example.com/article"

        with self._mock_public_host():
            with patch("app.extraction.fetcher._get_client") as mock_client:
                mock_client.return_value = MagicMock()
                with patch("app.extraction.fetcher._get_with_validated_redirects") as mock_get:
                    mock_get.side_effect = [
                        (blocked_response, 15.0),
                        (recovered_response, 25.0),
                    ]
                    with patch("app.extraction.fetcher._rate_limit_domain"):
                        result = fetch_url("https://example.com/article")

        assert result.status_code == 200
        assert "Recovered" in result.html
        assert mock_get.call_count == 2
        assert "User-Agent" not in mock_get.call_args_list[0].args[2]
        assert mock_get.call_args_list[1].args[2]["User-Agent"] == BROWSER_FALLBACK_USER_AGENT

    def test_401_falls_back_to_requests_browser_transport(self):
        blocked_response = MagicMock()
        blocked_response.status_code = 401
        blocked_response.headers = {}
        blocked_response.url = "https://example.com/article"
        blocked_response.content = b""
        blocked_response.encoding = "utf-8"

        recovered_response = self._mock_http_response(
            200, body=b"<html><head><title>Recovered</title></head></html>"
        )
        recovered_response.url = "https://www.example.com/article"

        with self._mock_public_host():
            with patch("app.extraction.fetcher._get_client") as mock_client:
                mock_client.return_value = MagicMock()
                with patch(
                    "app.extraction.fetcher._get_with_validated_redirects"
                ) as mock_httpx_get:
                    mock_httpx_get.return_value = (blocked_response, 10.0)
                    with patch(
                        "app.extraction.fetcher._requests_get_with_validated_redirects"
                    ) as mock_requests_get:
                        mock_requests_get.return_value = (recovered_response, 30.0)
                        with patch("app.extraction.fetcher._rate_limit_domain"):
                            result = fetch_url("https://example.com/article")

        assert result.status_code == 200
        assert "Recovered" in result.html
        assert result.url == "https://www.example.com/article"
        mock_requests_get.assert_called_once()

    def test_text_plain_response_falls_back_to_requests_browser_transport(self):
        plain_response = MagicMock()
        plain_response.status_code = 200
        plain_response.headers = {"Content-Type": "text/plain; charset=utf-8"}
        plain_response.url = "https://example.com/article"
        plain_response.content = b"# AI Agent Bracket Challenge"
        plain_response.encoding = "utf-8"

        recovered_response = self._mock_http_response(
            200,
            body=b"<html><head><meta property='og:title' content='Recovered'></head><body></body></html>",
        )
        recovered_response.url = "https://www.example.com/article"

        with self._mock_public_host():
            with patch("app.extraction.fetcher._get_client") as mock_client:
                mock_client.return_value = MagicMock()
                with patch(
                    "app.extraction.fetcher._get_with_validated_redirects"
                ) as mock_httpx_get:
                    mock_httpx_get.return_value = (plain_response, 12.0)
                    with patch(
                        "app.extraction.fetcher._requests_get_with_validated_redirects"
                    ) as mock_requests_get:
                        mock_requests_get.return_value = (recovered_response, 18.0)
                        with patch("app.extraction.fetcher._rate_limit_domain"):
                            result = fetch_url("https://example.com/article")

        assert result.status_code == 200
        assert result.content_type == "text/html"
        assert result.url == "https://www.example.com/article"
        mock_requests_get.assert_called_once()

    def test_cloudflare_challenge_is_classified_as_bot_protected(self):
        challenge_response = MagicMock()
        challenge_response.status_code = 403
        challenge_response.headers = {
            "Content-Type": "text/html; charset=UTF-8",
            "Server": "cloudflare",
        }
        challenge_response.url = "https://example.com/article"
        challenge_response.content = (
            b"<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"
        )
        challenge_response.encoding = "utf-8"

        with self._mock_public_host():
            with patch("app.extraction.fetcher._get_client") as mock_client:
                mock_client.return_value = MagicMock()
                with patch(
                    "app.extraction.fetcher._get_with_validated_redirects"
                ) as mock_httpx_get:
                    mock_httpx_get.return_value = (challenge_response, 18.0)
                    with patch(
                        "app.extraction.fetcher._requests_get_with_validated_redirects"
                    ) as mock_requests_get:
                        mock_requests_get.return_value = (challenge_response, 5.0)
                        with patch("app.extraction.fetcher._rate_limit_domain"):
                            result = fetch_url("https://example.com/article")

        assert result.status_code == 403
        assert result.error == "BOT_PROTECTED: cloudflare_challenge"

    def test_200_human_verification_page_is_classified_as_bot_protected(self):
        challenge_response = MagicMock()
        challenge_response.status_code = 200
        challenge_response.headers = {"Content-Type": "text/html; charset=UTF-8"}
        challenge_response.url = "https://example.com/article"
        challenge_response.content = (
            b"<html><body><h1>Verify you are human</h1><p>Enable JavaScript and cookies.</p></body></html>"
        )
        challenge_response.encoding = "utf-8"

        with self._mock_public_host():
            with patch("app.extraction.fetcher._get_client") as mock_client:
                mock_client.return_value = MagicMock()
                with patch(
                    "app.extraction.fetcher._get_with_validated_redirects"
                ) as mock_httpx_get:
                    mock_httpx_get.return_value = (challenge_response, 11.0)
                    with patch("app.extraction.fetcher._rate_limit_domain"):
                        result = fetch_url("https://example.com/article")

        assert result.status_code == 200
        assert result.error == "BOT_PROTECTED: human_verification"


# ═══════════════════════════════════════════════════════════════════════════════
# _rate_limit_domain — lock released before sleeping
# ═══════════════════════════════════════════════════════════════════════════════


class TestRateLimitDomain:
    """The domain lock must be released before time.sleep() is called."""

    def test_lock_released_before_sleep(self):
        """Verify that sleep is called *outside* the lock.

        Approach: patch time.sleep to assert that _domain_lock is NOT held
        when sleep is invoked.  If it were held, acquiring the lock inside
        the sleep callback would deadlock (Lock is not re-entrant).
        """
        from app.extraction import fetcher as fetcher_module

        lock_held_during_sleep = []

        def fake_sleep(duration):
            # Try to acquire the lock non-blocking; if we can, it's been released
            acquired = fetcher_module._domain_lock.acquire(blocking=False)
            lock_held_during_sleep.append(not acquired)
            if acquired:
                fetcher_module._domain_lock.release()

        # Force a wait by setting last request to now
        fetcher_module._domain_last_request["__test_domain__"] = time.monotonic()

        with patch("app.extraction.fetcher.time.sleep", side_effect=fake_sleep):
            # Use a 0.01s interval to ensure the wait fires
            _rate_limit_domain("__test_domain__", min_interval=0.01)

        # If this assertion fails, the lock was held during sleep → bug
        assert not any(lock_held_during_sleep), (
            "time.sleep was called while _domain_lock was held — threads on "
            "other domains would be serialized unnecessarily"
        )
