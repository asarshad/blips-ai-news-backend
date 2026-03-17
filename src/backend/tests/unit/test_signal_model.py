from app.models.signal import EnqueueStatusEnum, SignalSourceEnum


def test_signal_enums_use_lowercase_database_values():
    assert EnqueueStatusEnum.enums == ["pending", "ingested", "duplicate", "rejected"]
    assert SignalSourceEnum.enums == [
        "hn_top",
        "hn_best",
        "github_trending",
        "yt_trending",
        "discovery_leads",
    ]
