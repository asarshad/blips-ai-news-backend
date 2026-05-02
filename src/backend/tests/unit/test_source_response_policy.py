from datetime import datetime

from app.ingestion.source_response_policy import (
    ACTION_BLOCKED,
    ACTION_CANONICAL_REDIRECT,
    ACTION_GONE,
    ACTION_RATE_LIMITED,
    ACTION_TRANSIENT_ERROR,
    STATUS_COOLDOWN,
    STATUS_DISABLED,
    classify_http_response,
    cooldown_until_for_outcome,
    health_status_for_outcome,
)


def test_classifies_429_with_retry_after_as_rate_limited():
    outcome = classify_http_response(
        status_code=429,
        headers={"Retry-After": "120"},
        original_url="https://example.com/rss",
        final_url="https://example.com/rss",
    )

    assert outcome.action == ACTION_RATE_LIMITED
    assert outcome.retry_after_seconds == 120
    assert health_status_for_outcome(outcome) == STATUS_COOLDOWN


def test_classifies_permanent_redirect_as_canonical_redirect():
    outcome = classify_http_response(
        status_code=200,
        original_url="http://example.com/feed",
        final_url="https://example.com/feed",
        redirect_statuses=[301],
    )

    assert outcome.action == ACTION_CANONICAL_REDIRECT
    assert outcome.canonical_url == "https://example.com/feed"
    assert outcome.redirect_count == 1


def test_classifies_blocked_and_gone_sources():
    blocked = classify_http_response(status_code=403)
    gone = classify_http_response(status_code=410)

    assert blocked.action == ACTION_BLOCKED
    assert gone.action == ACTION_GONE
    assert health_status_for_outcome(gone) == STATUS_DISABLED


def test_transient_cooldown_grows_with_consecutive_failures():
    now = datetime(2026, 5, 1, 12, 0, 0)
    outcome = classify_http_response(status_code=503)

    assert outcome.action == ACTION_TRANSIENT_ERROR
    assert cooldown_until_for_outcome(outcome, now=now, consecutive_failures=3) == datetime(
        2026,
        5,
        1,
        12,
        15,
        0,
    )
