from app.integrations.youtube_channels import (
    CHANNEL_REGISTRY,
    ContentFormat,
    dedupe_channel_configs,
    get_channel_by_id,
    get_enabled_channels,
)


def test_enabled_channels_are_unique_by_channel_id():
    channel_ids = [channel.channel_id for channel in get_enabled_channels()]
    assert len(channel_ids) == len(set(channel_ids))


def test_dedupe_channel_configs_keeps_primary_channel_metadata():
    unique = {channel.channel_id: channel for channel in dedupe_channel_configs(CHANNEL_REGISTRY)}

    mkbhd = unique["UCBJycsmduvYEL83R_U4JriQ"]
    assert mkbhd.name == "Marques Brownlee (MKBHD)"
    assert mkbhd.content_format == ContentFormat.MIXED

    android = unique["UCVHFbqXqoYvEWM1Ddxl0QDg"]
    assert android.name == "Android Developers"
    assert android.enabled is True


def test_corrected_channel_ids_resolve_expected_channels():
    techlinked = get_channel_by_id("UCeeFfhMcJa1kjtfZAGskOCA")
    hardware_canucks = get_channel_by_id("UCTzLRZUgelatKZ4nyIKcAbg")

    assert techlinked is not None
    assert techlinked.name == "TechLinked"

    assert hardware_canucks is not None
    assert hardware_canucks.name == "Hardware Canucks"
