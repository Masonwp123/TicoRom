import asyncio
import hashlib
import json
import os

import httpx
import pytest

from ticorom.db import Database, connect
from ticorom.romm import RomMError
from ticorom.startup.platforms import parse_platform_map
from ticorom.sync import Syncer

MAP = parse_platform_map("""
exclude: [ps3]
platforms:
  gba: {tico_slug: gba, extensions: [gba, 7z]}
  ps2: {tico_slug: ps2, extensions: [chd]}
""")


# RomM's API with a library that tests can change between syncs.
class FakeRomM:

    def __init__(self):
        self.platforms = [
            {"id": 1, "slug": "gba", "fs_slug": "gba", "name": "Game Boy Advance", "rom_count": 0},
            {"id": 2, "slug": "ps2", "fs_slug": "ps2", "name": "PlayStation 2", "rom_count": 0},
            {"id": 3, "slug": "ps3", "fs_slug": "ps3", "name": "PlayStation 3", "rom_count": 0},
        ]
        self.roms: dict[int, list[dict]] = {1: [], 2: [], 3: []}
        self.down = False
        self.requests: list[httpx.Request] = []

    def add(self, rom_id, platform_id, file_name, **extra):
        folder = {1: "gba", 2: "ps2", 3: "ps3"}[platform_id]
        path = f"roms/{folder}/{file_name}"
        self.roms[platform_id].append({
            "id": rom_id, "platform_id": platform_id, "fs_name": file_name, "full_path": path,
            "name": extra.get("name", file_name.rsplit(".", 1)[0]), "summary": "A game.",
            "path_cover_small": f"/assets/romm/resources/roms/{platform_id}/{rom_id}/cover/small.png",
            "updated_at": "2026-11-20T00:00:00+00:00",
            "metadatum": {"genres": ["Adventure"], "first_release_date": 951782400000},
            "files": [{"id": rom_id * 10, "file_name": file_name, "full_path": path, "category": "game"}],
        })

    def remove(self, rom_id):
        for roms in self.roms.values():
            roms[:] = [rom for rom in roms if rom["id"] != rom_id]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused")
        if request.url.path == "/api/platforms":
            return httpx.Response(200, json=self.platforms)
        if request.url.path == "/api/roms":
            roms = self.roms[int(request.url.params["platform_ids"])]
            offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
            return httpx.Response(200, json={"items": roms[offset:offset + limit], "total": len(roms)})
        return httpx.Response(404)


@pytest.fixture
def env(make_settings, tmp_path):
    settings = make_settings()
    settings.config_path.mkdir()
    database = Database.in_config(settings.config_path)
    database.migrate()
    romm = FakeRomM()
    syncer = Syncer(settings, MAP, database, transport=httpx.MockTransport(romm.handler))

    def put(relative, data):
        path = settings.library_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    class Env:
        pass

    e = Env()
    e.settings, e.database, e.romm, e.syncer, e.put = settings, database, romm, syncer, put
    e.sync = lambda: asyncio.run(syncer.run())
    e.query = lambda sql, *args: [dict(row) for row in connect(database.path).execute(sql, args)]
    return e


def packages(env):
    return {row["id"]: row for row in env.query("SELECT * FROM packages")}


def test_first_sync_mirrors_romm_hashes_and_publishes(env):
    data = b"gba game"
    env.put("roms/gba/Quest (USA).7z", data)
    env.romm.add(7, 1, "Quest (USA).7z")
    env.romm.add(8, 3, "Excluded.iso")

    result = env.sync()

    assert (result.platforms, result.roms, result.published, result.hashed_files) == (2, 1, 1, 1)
    package = packages(env)["romm-home-7"]
    assert package["status"] == "published"
    assert package["version"] == hashlib.sha256(data).hexdigest()[:8]
    assert (package["tico_slug"], package["install"], package["filename"]) == ("gba", "tico/roms/gba", "Quest (USA).7z")

    rom = env.query("SELECT * FROM roms")[0]
    assert (rom["name"], rom["release_date"], json.loads(rom["genres"])) == ("Quest (USA)", 951782400, ["Adventure"])
    assert rom["cover_path"].endswith("/cover/small.png")
    # The excluded platform is never asked for its games
    assert all(r.url.params.get("platform_ids") != "3" for r in env.romm.requests)

    run = env.query("SELECT * FROM sync_runs")[0]
    assert run["status"] == "ok" and json.loads(run["stats"])["published"] == 1


def test_unchanged_files_are_not_hashed_again(env):
    env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()
    assert env.sync().hashed_files == 0


def test_a_changed_file_is_hashed_again_and_gets_a_new_version(env):
    path = env.put("roms/gba/Quest.7z", b"first")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()
    first = packages(env)["romm-home-7"]["version"]

    path.write_bytes(b"second, longer")
    os.utime(path, ns=(1, 2_000_000_000))
    result = env.sync()

    assert result.hashed_files == 1
    assert packages(env)["romm-home-7"]["version"] == hashlib.sha256(b"second, longer").hexdigest()[:8] != first


def test_games_removed_from_romm_disappear_but_their_hashes_stay(env):
    env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()

    env.romm.remove(7)
    env.sync()
    assert packages(env) == {}
    assert len(env.query("SELECT * FROM file_facts")) == 1  # the file is still on disk

    env.romm.add(7, 1, "Quest.7z")
    assert env.sync().hashed_files == 0


def test_hashes_of_files_gone_from_disk_are_dropped(env):
    path = env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()

    path.unlink()
    result = env.sync()
    assert env.query("SELECT * FROM file_facts") == []
    assert packages(env)["romm-home-7"]["skip_reason"] == "missing"
    assert result.skipped == {"missing": 1}


def test_skipped_games_are_recorded_with_a_reason(env):
    env.put("roms/gba/Manual.pdf", b"pdf")
    env.romm.add(7, 1, "Manual.pdf")
    result = env.sync()
    assert packages(env)["romm-home-7"]["skip_reason"] == "extension"
    assert result.skipped == {"extension": 1} and result.hashed_files == 0  # skipped files are never hashed


def test_a_failed_sync_keeps_the_last_good_state(env):
    env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()

    env.romm.down = True
    with pytest.raises(RomMError):
        env.sync()

    assert packages(env)["romm-home-7"]["status"] == "published"
    runs = env.query("SELECT status, error FROM sync_runs ORDER BY id")
    assert [run["status"] for run in runs] == ["ok", "failed"] and "cannot reach RomM" in runs[1]["error"]


def test_large_platforms_are_read_a_page_at_a_time(env):
    for rom_id in range(1, 251):
        env.put(f"roms/gba/Game {rom_id}.7z", str(rom_id).encode())
        env.romm.add(rom_id, 1, f"Game {rom_id}.7z")
    result = env.sync()
    assert result.roms == 250 and result.published == 250
    offsets = [r.url.params["offset"] for r in env.romm.requests if r.url.params.get("platform_ids") == "1"]
    assert offsets == ["0", "100", "200"]


def test_a_file_listed_twice_by_romm_is_kept_once(env):
    env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.romm.add(8, 1, "Quest.7z")
    env.romm.roms[1][1]["files"][0]["id"] = 999
    env.sync()
    assert len(env.query("SELECT * FROM rom_files")) == 1


def test_a_sync_writes_the_index_files(env):
    env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()
    folder = env.settings.cache_path / "index"
    combined = json.loads((folder / "_all.json").read_text())
    assert [p["id"] for p in combined["packages"]] == ["romm-home-7"]
    assert (folder / "gba.json").exists() and not (folder / "ps2.json").exists()
