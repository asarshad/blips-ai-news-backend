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


def test_curated_registry_scales_to_trusted_roster():
    enabled = get_enabled_channels()
    assert len(enabled) >= 100
    assert (
        len([channel for channel in enabled if channel.content_format == ContentFormat.SHORTS]) >= 3
    )
    assert (
        len([channel for channel in enabled if channel.content_format == ContentFormat.MIXED]) >= 20
    )

    unique = {channel.channel_id: channel for channel in dedupe_channel_configs(CHANNEL_REGISTRY)}
    mkbhd = unique["UCBJycsmduvYEL83R_U4JriQ"]
    assert mkbhd.name == "Marques Brownlee (MKBHD)"
    assert mkbhd.content_format == ContentFormat.MIXED


def test_corrected_channel_ids_resolve_expected_channels():
    techlinked = get_channel_by_id("UCeeFfhMcJa1kjtfZAGskOCA")
    hardware_canucks = get_channel_by_id("UCTzLRZUgelatKZ4nyIKcAbg")
    openai = get_channel_by_id("UCXZCJLdBC09xxGZ6gcdrc6A")

    assert techlinked is not None
    assert techlinked.name == "TechLinked"

    assert hardware_canucks is not None
    assert hardware_canucks.name == "Hardware Canucks"

    assert openai is not None
    assert openai.name == "OpenAI"
