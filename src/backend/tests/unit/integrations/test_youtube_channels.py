from app.integrations.youtube_channels import (
    CHANNEL_REGISTRY,
    ContentFormat,
    dedupe_channel_configs,
    get_bootstrap_channels,
    get_channel_by_id,
    get_channel_by_name,
    get_enabled_channels,
    get_expansion_channels,
    get_primary_channels,
)


def test_enabled_channels_are_unique_by_channel_id():
    channel_ids = [channel.channel_id for channel in get_enabled_channels()]
    assert len(channel_ids) == len(set(channel_ids))


def test_curated_registry_scales_to_trusted_roster():
    enabled = get_enabled_channels()
    primary = get_primary_channels()
    expansion = get_expansion_channels()
    assert len(enabled) >= 100
    assert len(primary) >= 100
    assert len(expansion) >= 8
    assert (
        len([channel for channel in enabled if channel.content_format == ContentFormat.SHORTS]) >= 3
    )
    assert (
        len([channel for channel in enabled if channel.content_format == ContentFormat.MIXED]) >= 20
    )

    unique = {channel.channel_id: channel for channel in dedupe_channel_configs(CHANNEL_REGISTRY)}
    mkbhd = unique["UCBJycsmduvYEL83R_U4JriQ"]
    digital_foundry = unique["UC9PBzalIcEQCsiIkq36PyUA"]
    hardware_unboxed = unique["UCI8iQa1hv7oV_Z8D35vVuSg"]
    google_for_developers = unique["UC_x5XG1OV2P6uZZ5FSM9Ttw"]
    assert mkbhd.name == "Marques Brownlee (MKBHD)"
    assert mkbhd.content_format == ContentFormat.MIXED
    assert digital_foundry.name == "Digital Foundry"
    assert digital_foundry.content_format == ContentFormat.LONG_FORM
    assert digital_foundry.ingestion_stream.value == "expansion"
    assert hardware_unboxed.name == "Hardware Unboxed"
    assert hardware_unboxed.ingestion_stream.value == "primary"
    assert google_for_developers.name == "Google for Developers"


def test_bootstrap_channels_and_age_overrides_cover_new_long_form_sources():
    bootstrap_names = {channel.name for channel in get_bootstrap_channels()}

    assert "Ars Technica" in bootstrap_names
    assert "Dwarkesh Podcast" in bootstrap_names
    assert "MKBHD Shorts" in bootstrap_names

    dwarkesh = get_channel_by_name("Dwarkesh Podcast")
    dtns = get_channel_by_name("Daily Tech News Show")

    assert dwarkesh is not None
    assert dwarkesh.fresh_published_hours == 72
    assert dwarkesh.backfill_created_hours == 24
    assert dwarkesh.evergreen_max_days == 21
    assert dwarkesh.has_age_overrides is True

    assert dtns is not None
    assert dtns.fresh_published_hours == 48
    assert dtns.backfill_created_hours == 24
    assert dtns.evergreen_max_days == 14


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


def test_channel_name_lookup_prefers_high_confidence_matches():
    mkbhd = get_channel_by_name("Marques Brownlee")
    apple = get_channel_by_name("Apple")
    unknown = get_channel_by_name("Unknown Tech Channel")

    assert mkbhd is not None
    assert mkbhd.name == "Marques Brownlee (MKBHD)"

    assert apple is not None
    assert apple.name == "Apple"

    assert unknown is None
