import gzip
import json

import pytest

from ticorom import index
from ticorom.db import connect, migrate

SHA_A = "aa" * 32
SHA_B = "bb" * 32


@pytest.fixture
def settings(make_settings):
    return make_settings()


@pytest.fixture
def conn(settings):
    settings.config_path.mkdir()
    migrate(settings.config_path / "ticorom.db")
    conn = connect(settings.config_path / "ticorom.db")
    conn.executescript("""
        INSERT INTO platforms VALUES (1, 'gba', 'gba', 'Game Boy Advance'), (2, 'ngc', 'ngc', 'GameCube');
        INSERT INTO roms (id, platform_id, name, fs_name, summary, genres, release_date, romm_updated_at) VALUES
          (7, 1, 'Quest', 'Quest (USA) (Rev 1).7z', 'A long   adventure.', '["Adventure"]', 951782400, ''),
          (8, 2, 'Racer', 'Racer.rvz', NULL, '["Racing", "Sport"]', 951782400, ''),
          (9, 1, 'Later', 'Later.7z', NULL, '[]', NULL, '');
    """)
    conn.executemany("INSERT INTO file_facts (path, size, mtime_ns, sha256) VALUES (?, ?, 1, ?)", [
        ("roms/gba/Quest (USA) (Rev 1).7z", 1000, SHA_A),
        ("roms/ngc/Racer.rvz", 2000, SHA_B),
        ("roms/gba/Later.7z", 3000, None),
    ])
    conn.executemany("INSERT INTO packages VALUES (?, ?, ?, ?, ?, ?, 'file', ?, ?, NULL)", [
        ("romm-home-7", 7, "roms/gba/Quest (USA) (Rev 1).7z", "gba", "tico/roms/gba", "Quest (USA) (Rev 1).7z",
         SHA_A[:8], "published"),
        ("romm-home-8", 8, "roms/ngc/Racer.rvz", "gc", "tico/roms/gc", "Racer.rvz", SHA_B[:8], "published"),
        ("romm-home-9", 9, "roms/gba/Later.7z", "gba", "tico/roms/gba", "Later.7z", None, "pending"),
    ])
    return conn


def read(settings, name):
    return json.loads((settings.cache_path / "index" / f"{name}.json").read_text())


def test_build_writes_a_combined_index_and_one_per_console(conn, settings):
    assert index.build(conn, settings) == 2
    combined = read(settings, "_all")
    assert combined["schema"] == 1 and combined["name"] == "TicoRom (home)"
    assert [p["id"] for p in combined["packages"]] == ["romm-home-7", "romm-home-8"]  # pending games are left out
    assert [p["id"] for p in read(settings, "gba")["packages"]] == ["romm-home-7"]
    assert read(settings, "gc")["name"] == "TicoRom (home): GameCube"


def test_package_fields(conn, settings):
    index.build(conn, settings)
    quest = read(settings, "gba")["packages"][0]
    assert quest == {
        "id": "romm-home-7",
        "name": "Quest",
        "description": "A long adventure.",
        "tagline": "USA · Rev 1",
        "category": "game",
        "tags": ["gba"],
        "version": SHA_A[:8],
        "type": "zip",
        "extract": False,
        "url": f"https://tico.example/r/{index.TOKEN_PLACEHOLDER}/dl/romm-home-7/{SHA_A[:8]}/Quest%20%28USA%29%20%28Rev%201%29.7z",
        "filename": "Quest (USA) (Rev 1).7z",
        "install": "tico/roms/gba",
        "size": 1000,
        "sha256": SHA_A,
    }
    racer = read(settings, "gc")["packages"][0]
    assert racer["tagline"] == "Racing · 2000" and "description" not in racer


def test_consoles_with_nothing_published_lose_their_index(conn, settings):
    index.build(conn, settings)
    conn.execute("DELETE FROM packages WHERE tico_slug = 'gc'")
    index.build(conn, settings)
    assert not (settings.cache_path / "index" / "gc.json").exists()
    assert index.IndexFiles(settings).consoles() == ["gba"]


@pytest.mark.parametrize("fs_name, genres, date, expected", [
    ("Game (Europe) (En,Fr) [b].zip", [], None, "Europe · En,Fr · b"),
    ("Game.zip", ["Puzzle", "Action"], 0, "Puzzle"),
    ("Game.zip", [], None, ""),
])
def test_tagline(fs_name, genres, date, expected):
    assert index.tagline(fs_name, genres, date) == expected


def test_long_descriptions_are_cut_at_a_word():
    text = index._shorten("word " * 200, 50)
    assert text.endswith("…") and len(text) <= 51 and "  " not in text


def test_render_fills_in_the_token(conn, settings):
    index.build(conn, settings)
    files = index.IndexFiles(settings)
    body = files.render("t" * 32, None, None, gz=False)
    assert index.TOKEN_PLACEHOLDER.encode() not in body
    assert f"/r/{'t' * 32}/dl/romm-home-7/".encode() in body
    assert gzip.decompress(files.render("t" * 32, None, None, gz=True)) == body


def test_render_respects_a_tokens_consoles(conn, settings):
    index.build(conn, settings)
    files = index.IndexFiles(settings)
    limited = json.loads(files.render("t" * 32, ["gc"], None, gz=False))
    assert [p["id"] for p in limited["packages"]] == ["romm-home-8"]
    assert files.render("t" * 32, ["gc"], "gba", gz=False) is None
    assert files.render("t" * 32, None, "n64", gz=False) is None
    assert files.render("t" * 32, None, "../_all", gz=False) is None


def test_before_the_first_sync_the_index_is_empty(settings):
    body = json.loads(index.IndexFiles(settings).render("t" * 32, None, None, gz=False))
    assert body["packages"] == [] and body["name"] == "TicoRom (home)"
