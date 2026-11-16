import pytest

from ticorom.startup.platforms import (
    MAP_FILE_NAME, PlatformMapError, Status, default_map_text, load_platform_map, parse_platform_map,
)


def test_default_map_is_valid_and_covers_the_library():
    platform_map = parse_platform_map(default_map_text())
    assert platform_map.status("gba") is Status.PUBLISHED
    assert platform_map.status("ps3") is Status.EXCLUDED
    assert platform_map.status("saturn") is Status.UNMAPPED
    # RomM and tico name these two consoles differently.
    assert platform_map.entry("ngc").tico_slug == "gc"
    assert platform_map.entry("gamegear").install == "tico/roms/game-gear"
    assert platform_map.entry("ps2").chd_types == ["cd"]


def test_install_folder_defaults_to_the_tico_slug():
    platform_map = parse_platform_map("""
platforms:
  snes: {tico_slug: snes, extensions: [sfc]}
  n64: {tico_slug: n64, install: /roms/custom/n64/, extensions: [z64]}
""")
    assert platform_map.entry("snes").install == "tico/roms/snes"
    assert platform_map.entry("n64").install == "roms/custom/n64"


def test_extensions_are_normalized():
    platform_map = parse_platform_map("platforms: {gba: {tico_slug: gba, extensions: [.GBA, 7z]}}")
    assert platform_map.entry("gba").extensions == ["gba", "7z"]


def test_disabled_platform_is_not_published():
    platform_map = parse_platform_map("platforms: {gba: {tico_slug: gba, extensions: [gba], enabled: false}}")
    assert platform_map.status("gba") is Status.DISABLED


def test_slug_is_the_fallback_when_the_folder_name_is_not_in_the_map():
    platform_map = parse_platform_map("""
exclude: [xbox]
platforms: {gba: {tico_slug: gba, extensions: [gba]}}
""")
    assert platform_map.status("gameboy-advance", "gba") is Status.PUBLISHED
    assert platform_map.status("original-xbox", "xbox") is Status.EXCLUDED


@pytest.mark.parametrize("text, expected", [
    ("exclude: [gba]\nplatforms: {gba: {tico_slug: gba, extensions: [gba]}}", "both 'platforms' and 'exclude': gba"),
    ("platforms: {gba: {tico_slug: gba}}", "platforms.gba.extensions"),
    ("platforms: {gba: {tico_slug: gba, extensions: [gba], instal: x}}", "platforms.gba.instal"),
    ("platforms: {gba: {tico_slug: Game Boy, extensions: [gba]}}", "platforms.gba.tico_slug"),
    ("platforms: {ps2: {tico_slug: ps2, extensions: [chd], chd_types: [bluray]}}", "platforms.ps2.chd_types"),
    ("platforms: [gba", "not valid YAML"),
    ("- gba", "must be a mapping"),
])
def test_invalid_maps_are_rejected_with_a_pointer_to_the_problem(text, expected):
    with pytest.raises(PlatformMapError) as caught:
        parse_platform_map(text)
    assert expected in str(caught.value)


def test_invalid_map_file_is_rejected(tmp_path):
    (tmp_path / MAP_FILE_NAME).write_text("platforms: {gba: {tico_slug: gba}}")
    with pytest.raises(PlatformMapError, match="platforms.gba.extensions"):
        load_platform_map(tmp_path)


def test_first_run_creates_a_missing_config_folder(tmp_path):
    config_path = tmp_path / "config"
    platform_map, created = load_platform_map(config_path)
    assert created and (config_path / MAP_FILE_NAME).exists()
    assert platform_map.status("gba") is Status.PUBLISHED


def test_first_run_creates_the_map_and_later_runs_keep_the_users_copy(tmp_path):
    platform_map, created = load_platform_map(tmp_path)
    assert created and platform_map.status("gba") is Status.PUBLISHED

    (tmp_path / MAP_FILE_NAME).write_text("exclude: [gba]\n")
    platform_map, created = load_platform_map(tmp_path)
    assert not created and platform_map.status("gba") is Status.EXCLUDED
