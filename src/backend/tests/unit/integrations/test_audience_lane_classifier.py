"""Tests for classify_audience_lane() in llm_client.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock


def _make_client():
    from app.integrations.llm_client import LLMClient

    client = LLMClient(provider="openai", api_key="sk-test-fake-key-12345678901234567890")
    return client


def _classify(response_json: str, *, title="Test", summary="Test summary.", source="Test"):
    client = _make_client()
    client.chat = Mock(return_value=SimpleNamespace(content=response_json))
    return client.classify_audience_lane(title=title, summary=summary, source=source)


# ---------------------------------------------------------------------------
# Happy-path — GENERAL_PUBLIC
# ---------------------------------------------------------------------------


def test_general_public_iphone_launch():
    result = _classify(
        '{"lane":"GENERAL_PUBLIC","confidence":0.98,"reason":"Major consumer device launch."}',
        title="Apple announces iPhone 17 with titanium body and A19 chip",
        summary="Apple unveiled the iPhone 17 at its September event...",
        source="The Verge",
    )
    assert result.lane == "GENERAL_PUBLIC"
    assert result.confidence == 0.98
    assert "consumer" in result.reason.lower()


def test_general_public_chatgpt_free_tier():
    result = _classify(
        '{"lane":"GENERAL_PUBLIC","confidence":0.95,"reason":"Consumer AI product affecting millions."}',
        title="OpenAI launches free ChatGPT tier with GPT-4o access",
        source="TechCrunch",
    )
    assert result.lane == "GENERAL_PUBLIC"
    assert result.confidence == 0.95


def test_general_public_big_tech_layoffs():
    result = _classify(
        '{"lane":"GENERAL_PUBLIC","confidence":0.92,"reason":"Mainstream tech industry news."}',
        title="Google lays off 12,000 employees across multiple divisions",
        source="Bloomberg",
    )
    assert result.lane == "GENERAL_PUBLIC"


def test_general_public_tiktok_ban():
    result = _classify(
        '{"lane":"GENERAL_PUBLIC","confidence":0.97,"reason":"Widely used consumer app, mainstream news."}',
        title="TikTok faces US ban unless divested within 90 days",
        source="Reuters",
    )
    assert result.lane == "GENERAL_PUBLIC"


# ---------------------------------------------------------------------------
# Happy-path — TECHIES
# ---------------------------------------------------------------------------


def test_techies_rust_edition():
    result = _classify(
        '{"lane":"TECHIES","confidence":0.97,"reason":"Rust language edition for developers only."}',
        title="Rust 2024 Edition stabilises async-fn-in-trait syntax",
        source="InfoQ",
    )
    assert result.lane == "TECHIES"
    assert result.confidence == 0.97


def test_techies_postgres_vacuum():
    result = _classify(
        '{"lane":"TECHIES","confidence":0.96,"reason":"Database internals for DBAs and backend engineers."}',
        title="PostgreSQL 17 improves autovacuum and buffer manager performance",
        source="The New Stack",
    )
    assert result.lane == "TECHIES"


def test_techies_kubernetes_release():
    result = _classify(
        '{"lane":"TECHIES","confidence":0.98,"reason":"Platform engineering tooling, not consumer-facing."}',
        title="Kubernetes 1.32 graduates CEL admission validation to stable",
        source="CNCF Blog",
    )
    assert result.lane == "TECHIES"


def test_techies_cve_disclosure():
    result = _classify(
        '{"lane":"TECHIES","confidence":0.95,"reason":"CVE requires security engineering context to appreciate."}',
        title="CVE-2025-1234: Critical RCE in Apache Struts affects enterprise deployments",
        source="Bleeping Computer",
    )
    assert result.lane == "TECHIES"


# ---------------------------------------------------------------------------
# Result dataclass fields
# ---------------------------------------------------------------------------


def test_result_has_all_fields():
    result = _classify('{"lane":"TECHIES","confidence":0.90,"reason":"Developer tooling."}')
    assert hasattr(result, "lane")
    assert hasattr(result, "confidence")
    assert hasattr(result, "reason")


def test_confidence_clamped_above_1():
    result = _classify('{"lane":"GENERAL_PUBLIC","confidence":1.5,"reason":"High confidence."}')
    assert result.confidence == 1.0


def test_confidence_clamped_below_0():
    result = _classify('{"lane":"TECHIES","confidence":-0.3,"reason":"Low confidence."}')
    assert result.confidence == 0.0


def test_reason_truncated_at_255():
    long_reason = "x" * 300
    result = _classify(f'{{"lane":"TECHIES","confidence":0.80,"reason":"{long_reason}"}}')
    assert len(result.reason) <= 255


# ---------------------------------------------------------------------------
# Normalisation — case insensitivity and whitespace
# ---------------------------------------------------------------------------


def test_lane_uppercased_from_lowercase():
    result = _classify('{"lane":"general_public","confidence":0.85,"reason":"Fine."}')
    assert result.lane == "GENERAL_PUBLIC"


def test_lane_uppercased_mixed_case():
    result = _classify('{"lane":"Techies","confidence":0.85,"reason":"Fine."}')
    assert result.lane == "TECHIES"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_invalid_lane_value_raises():
    import pytest

    with pytest.raises((ValueError, RuntimeError)):
        _classify('{"lane":"BOTH","confidence":0.80,"reason":"Neither."}')


def test_malformed_json_raises():
    import pytest

    with pytest.raises((ValueError, RuntimeError)):
        _classify("not json at all")


def test_empty_response_raises():
    import pytest

    with pytest.raises((ValueError, RuntimeError)):
        _classify("")


def test_json_fence_stripped():
    """Classifier should accept ```json ... ``` wrapped responses."""
    result = _classify(
        '```json\n{"lane":"GENERAL_PUBLIC","confidence":0.91,"reason":"Consumer news."}\n```'
    )
    assert result.lane == "GENERAL_PUBLIC"


# ---------------------------------------------------------------------------
# AudienceLaneResult exported
# ---------------------------------------------------------------------------


def test_audience_lane_result_importable():
    from app.integrations.llm_client import AudienceLaneResult

    r = AudienceLaneResult(lane="TECHIES", confidence=0.9, reason="test")
    assert r.lane == "TECHIES"
