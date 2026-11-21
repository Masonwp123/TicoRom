import pytest

from ticorom.library import FAT32_LIMIT
from ticorom.packages import (
    PENDING, PUBLISHED, SKIPPED, FileFact, GameFile, Package, _resolve_name_clashes, decide, package_id,
    safe_filename,
)
from ticorom.startup.platforms import parse_platform_map

MAP = parse_platform_map("""
platforms:
  gba: {tico_slug: gba, extensions: [gba, 7z, zip]}
  ps2: {tico_slug: ps2, extensions: [chd, iso], chd_types: [cd]}
  arcade: {tico_slug: fbneo, extensions: [zip], zip_policy: extract}
""")
SHA = "9f2c41aa" + "0" * 56


def run(settings, file_name, platform="gba", fact=FileFact(1024, SHA, None), files=None):
    path = f"roms/{platform}/{file_name}"
    files = files if files is not None else [GameFile(path, file_name, "game")]
    facts = {path: fact} if fact else {}
    return decide(42, file_name, files, MAP.platforms[platform], MAP, facts, settings)


def test_a_hashed_game_is_published(make_settings):
    package = run(make_settings(), "Example Quest (USA).7z")
    assert package.status == PUBLISHED and package.skip_reason is None
    assert package.id == "romm-home-42"
    assert package.version == "9f2c41aa"
    assert (package.tico_slug, package.install, package.mode) == ("gba", "tico/roms/gba", "file")
    assert package.filename == "Example Quest (USA).7z"
    assert package.path == "roms/gba/Example Quest (USA).7z"


def test_a_game_waits_for_its_hash(make_settings):
    package = run(make_settings(), "Game.7z", fact=FileFact(1024, None, None))
    assert package.status == PENDING and package.version is None


@pytest.mark.parametrize("kwargs, reason", [
    ({"file_name": "Game.nds"}, "extension"),
    ({"file_name": "Game.7z", "fact": None}, "missing"),
    ({"file_name": "Game.iso", "platform": "ps2", "fact": FileFact(FAT32_LIMIT, SHA, None)}, "over_4gib"),
    ({"file_name": "Game.chd", "platform": "ps2", "fact": FileFact(1024, SHA, "dvd")}, "chd_type"),
    ({"file_name": "game.zip", "platform": "arcade"}, "extract_mode"),
])
def test_skip_reasons(make_settings, kwargs, reason):
    package = run(make_settings(), **kwargs)
    assert package.status == SKIPPED and package.skip_reason == reason


def test_the_4gib_rule_can_be_turned_off(make_settings):
    package = run(make_settings(skip_files_over_4gib=False), "Game.iso", platform="ps2",
                  fact=FileFact(FAT32_LIMIT, SHA, None))
    assert package.status == PUBLISHED


def test_a_cd_type_chd_is_published(make_settings):
    package = run(make_settings(), "Game.chd", platform="ps2", fact=FileFact(1024, SHA, "cd"))
    assert package.status == PUBLISHED


def test_a_game_made_of_several_files_waits_for_bundle_mode(make_settings):
    files = [GameFile("roms/gba/Game/a.gba", "a.gba", None), GameFile("roms/gba/Game/b.gba", "b.gba", None)]
    package = run(make_settings(), "Game", files=files)
    assert package.status == SKIPPED and package.skip_reason == "multi_file"


def test_manuals_and_extras_do_not_count_as_game_files(make_settings):
    files = [GameFile("roms/gba/Game.7z", "Game.7z", "game"), GameFile("roms/gba/Game.pdf", "Game.pdf", "manual")]
    package = run(make_settings(), "Game.7z", files=files)
    assert package.status == PUBLISHED and package.path == "roms/gba/Game.7z"


@pytest.mark.parametrize("name, expected", [
    ("Example Quest (USA).gba", "Example Quest (USA).gba"),
    ('Who? What: "Why" <Now>|.gba', "Who What Why Now.gba"),
    ("Spaced   out \t name.gba", "Spaced out name.gba"),
    ("Trailing dots... ", "Trailing dots"),
    ("???", "fallback.gba"),
])
def test_safe_filename(name, expected):
    assert safe_filename(name, "fallback.gba") == expected


def test_long_file_names_keep_their_extension():
    name = safe_filename("A" * 300 + ".chd", "x.chd")
    assert len(name) == 200 and name.endswith(".chd")


def test_package_id():
    assert package_id("home", 4821) == "romm-home-4821"


def test_a_game_inside_a_folder_is_named_after_the_folder(make_settings):
    files = [GameFile("roms/ps2/Game A/disc.chd", "disc.chd", None, is_top_level=False)]
    package = decide(42, "Game A", files, MAP.platforms["ps2"], MAP,
                     {"roms/ps2/Game A/disc.chd": FileFact(1024, SHA, "cd")}, make_settings())
    assert package.status == PUBLISHED and package.filename == "Game A.chd"


def test_a_game_without_files(make_settings):
    package = decide(42, "Ghost.7z", [], MAP.platforms["gba"], MAP, {}, make_settings())
    assert package.status == SKIPPED and package.skip_reason == "no_file"


@pytest.mark.parametrize("path", ["roms/gba/../../outside.7z", "/srv/outside.7z", "roms\\gba\\outside.7z"])
def test_paths_outside_the_library_are_skipped(make_settings, path):
    files = [GameFile(path, "outside.7z", "game")]
    package = decide(42, "outside.7z", files, MAP.platforms["gba"], MAP, {path: FileFact(10, SHA, None)},
                     make_settings())
    assert package.status == SKIPPED and package.skip_reason == "unsafe_path"


def test_a_briefly_missing_file_is_skipped_even_though_its_hash_is_kept(make_settings):
    files = [GameFile("roms/gba/Quest.7z", "Quest.7z", "game")]
    facts = {"roms/gba/Quest.7z": FileFact(1024, SHA, None)}
    package = decide(42, "Quest.7z", files, MAP.platforms["gba"], MAP, facts, make_settings(),
                     frozenset({"roms/gba/Quest.7z"}))
    assert package.status == SKIPPED and package.skip_reason == "missing"


def _package(rom_id, filename, status=PUBLISHED, install="tico/roms/gba"):
    return Package(f"romm-home-{rom_id}", rom_id, f"roms/x/{rom_id}", "gba", install, filename, "file", "v",
                   status, None)


def test_name_clashes_are_resolved_with_the_rom_id():
    resolved = _resolve_name_clashes([_package(9, "QUEST.7z"), _package(3, "Quest.7z"), _package(5, "Other.7z"),
                                      _package(7, "Quest.7z", install="tico/roms/snes")])
    # FAT32 and exFAT ignore case, so QUEST.7z clashes with Quest.7z; another folder does not clash
    assert {p.rom_id: p.filename for p in resolved} == {3: "Quest.7z", 9: "QUEST (9).7z", 5: "Other.7z",
                                                        7: "Quest.7z"}


def test_skipped_games_do_not_take_a_name():
    resolved = _resolve_name_clashes([_package(3, "Quest.7z", status=SKIPPED), _package(9, "Quest.7z")])
    assert {p.rom_id: p.filename for p in resolved} == {3: "Quest.7z", 9: "Quest.7z"}
