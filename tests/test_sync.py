import asyncio
import hashlib
import json
import os
import threading

import httpx
import pytest

from ticorom.db import Database, connect
from ticorom.romm import RomMError
from ticorom.startup.platforms import parse_platform_map
from ticorom.sync import LOCK_FILE_NAME, LibraryUnavailableError, Syncer, SyncBusyError, _SyncLock

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
        self.assets: dict[str, bytes] = {}   # cover images, by path

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
        if request.url.path in self.assets:
            data = self.assets[request.url.path]
            if request.method == "HEAD":
                return httpx.Response(200, headers={"Content-Length": str(len(data))})
            return httpx.Response(200, content=data)
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


def test_a_missing_file_keeps_its_hash_and_is_not_hashed_again_when_back(env):
    env.put("roms/gba/Other.7z", b"other")
    env.romm.add(6, 1, "Other.7z")
    path = env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()

    away = path.with_name("away")
    path.rename(away)
    result = env.sync()
    assert packages(env)["romm-home-7"]["skip_reason"] == "missing"
    assert result.skipped == {"missing": 1}
    assert len(env.query("SELECT * FROM file_facts WHERE sha256 IS NOT NULL")) == 2   # hash kept

    away.rename(path)
    assert env.sync().hashed_files == 0
    assert packages(env)["romm-home-7"]["status"] == "published"


def test_an_unmounted_library_changes_nothing(env):
    for rom_id in (6, 7):
        env.put(f"roms/gba/Game {rom_id}.7z", bytes([rom_id]))
        env.romm.add(rom_id, 1, f"Game {rom_id}.7z")
    env.sync()
    index_before = (env.settings.cache_path / "index" / "_all.json").read_text()

    library = env.settings.library_path
    library.rename(library.with_name("library-away"))
    library.mkdir()                                       # the empty mount point
    with pytest.raises(LibraryUnavailableError, match="Is the library mounted"):
        env.sync()

    assert {p["status"] for p in packages(env).values()} == {"published"}
    assert (env.settings.cache_path / "index" / "_all.json").read_text() == index_before
    assert env.query("SELECT status FROM sync_runs ORDER BY id DESC LIMIT 1")[0]["status"] == "failed"


def test_hashes_are_dropped_once_romm_no_longer_lists_the_file_and_it_is_gone(env):
    env.put("roms/gba/Other.7z", b"other")
    env.romm.add(6, 1, "Other.7z")
    path = env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()
    env.romm.remove(7)
    path.unlink()
    env.sync()
    assert [row["path"] for row in env.query("SELECT path FROM file_facts")] == ["roms/gba/Other.7z"]


def test_paths_outside_the_library_are_never_read(env):
    outside = env.settings.library_path.parent / "outside.7z"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"not a game")
    env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.romm.add(8, 1, "x.7z")
    env.romm.roms[1][1]["files"][0]["full_path"] = "roms/gba/../../outside.7z"
    env.sync()
    assert packages(env)["romm-home-8"]["skip_reason"] == "unsafe_path"
    assert all(".." not in row["path"] for row in env.query("SELECT path FROM file_facts"))


def test_only_one_sync_runs_at_a_time(env):
    held = _SyncLock(env.settings.config_path / LOCK_FILE_NAME)
    try:
        with pytest.raises(SyncBusyError):
            env.sync()
    finally:
        held.release()
    env.sync()   # free again


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


def test_a_sync_fetches_covers_and_lists_them_as_icons(env):
    png = b"\x89PNG\r\n\x1a\n" + b"cover"
    env.romm.assets["/assets/romm/resources/roms/1/7/cover/small.png"] = png
    env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()

    [cover] = env.query("SELECT * FROM covers")
    assert cover["rom_id"] == 7 and cover["file"].endswith(".png")
    assert (env.settings.cache_path / "covers" / cover["file"]).read_bytes() == png
    combined = json.loads((env.settings.cache_path / "index" / "_all.json").read_text())
    assert combined["packages"][0]["icon"] == f"https://tico.example/r/@@TICOROM_TOKEN@@/img/{cover['file']}"

    # Nothing changed: the cover is not downloaded again
    env.romm.requests.clear()
    env.sync()
    assert not [r for r in env.romm.requests if r.method == "GET" and r.url.path.startswith("/assets/")]


def test_covers_of_removed_games_are_deleted(env):
    env.romm.assets["/assets/romm/resources/roms/1/7/cover/small.png"] = b"\x89PNG\r\n\x1a\nx"
    env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()
    env.romm.remove(7)
    env.sync()
    assert env.query("SELECT * FROM covers") == []
    assert list((env.settings.cache_path / "covers").iterdir()) == []


def test_a_game_without_a_cover_has_no_icon(env):
    env.put("roms/gba/Quest.7z", b"data")
    env.romm.add(7, 1, "Quest.7z")
    env.sync()
    combined = json.loads((env.settings.cache_path / "index" / "_all.json").read_text())
    assert "icon" not in combined["packages"][0]


def test_the_background_loop_syncs_early_when_woken():
    from ticorom.sync import sync_forever

    class CountingSyncer:
        runs = 0

        async def run(self, stop):
            self.runs += 1

    async def scenario():
        syncer, stop, wake = CountingSyncer(), threading.Event(), asyncio.Event()
        loop = asyncio.create_task(sync_forever(syncer, 60, stop, wake))   # interval: an hour
        await asyncio.sleep(0.05)
        assert syncer.runs == 1
        wake.set()                                                         # e.g. a download found a changed file
        await asyncio.sleep(0.05)
        assert syncer.runs == 2
        stop.set()
        loop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop

    asyncio.run(scenario())


def test_a_failed_sync_is_retried_soon_rather_than_after_the_full_interval(monkeypatch):
    import ticorom.sync as sync_module
    from ticorom.sync import sync_forever

    monkeypatch.setattr(sync_module, "RETRY_AFTER_FAILURE_MINUTES", 0.001)   # 60 ms

    class FailingSyncer:
        runs = 0

        async def run(self, stop):
            self.runs += 1
            raise RomMError("cannot reach RomM", unreachable=True)

    async def scenario():
        syncer, stop = FailingSyncer(), threading.Event()
        loop = asyncio.create_task(sync_forever(syncer, 60, stop))           # interval: an hour
        await asyncio.sleep(0.3)
        stop.set()
        loop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop
        return syncer.runs

    assert asyncio.run(scenario()) >= 3
