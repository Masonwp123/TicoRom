"""Cover images (DESIGN.md section 7.3, "icon").

During a sync, each game gets one cover, saved in CACHE_PATH/covers/:

  - RomM offers a small and a large cover, and with its WebP conversion turned
    on, a WebP copy of each. The smallest one tico can show is used.
  - "Can show" is decided from the file's first bytes, not its name: tico shows
    PNG, JPEG, WebP, GIF and BMP, but not AVIF (tested on hardware).
  - A cover is downloaded again only when RomM's cover changes (RomM puts a
    timestamp in its cover paths, so a changed cover has a different path), or
    when a WebP copy appears for a game whose cover is not WebP yet.
  - The file name holds a fingerprint of the image, e.g. "4821-9f2c41aa.png".
    tico fetches each image once and never asks whether it changed, so a new
    cover must get a new address.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from ticorom.romm import RomMClient, RomMError, RomMRom

log = logging.getLogger("ticorom.covers")

COVERS_DIR = "covers"
DOWNLOADS_AT_ONCE = 4
MAX_COVER_BYTES = 16 * 1024 * 1024   # anything bigger is not a cover
FILE_NAME_RE = re.compile(r"[0-9]+-[0-9a-f]{8}\.(png|jpg|webp|gif|bmp)")
CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp"}


@dataclass(frozen=True)
class Cover:
    rom_id: int
    source: str          # RomM's cover paths when this was decided; a change means "look again"
    file: str | None     # file name in CACHE_PATH/covers, or None if RomM has no usable cover


# The image format from the first bytes of a file: "png", "jpg", "webp", "gif", "bmp",
# or None for anything tico cannot show (AVIF, HTML error pages, ...).
def image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    return None


# The covers decided by earlier syncs, by game
def load_known(conn: sqlite3.Connection) -> dict[int, Cover]:
    return {row["rom_id"]: Cover(row["rom_id"], row["source"], row["file"])
            for row in conn.execute("SELECT rom_id, source, file FROM covers")}


# Decide every game's cover, downloading only those that are new or changed
async def fetch_all(romm: RomMClient, roms: list[RomMRom], known: dict[int, Cover], cache_path: Path) -> list[Cover]:
    folder = cache_path / COVERS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    limit = asyncio.Semaphore(DOWNLOADS_AT_ONCE)
    counts = {"downloaded": 0}

    async def one(rom: RomMRom) -> Cover | None:
        source = _source(rom)
        previous = known.get(rom.id)
        unchanged = (previous is not None and previous.source == source
                     and (previous.file is None or (folder / previous.file).is_file()))
        # Unchanged since last time and already a WebP: nothing to do, not even a request
        if unchanged and previous.file and previous.file.endswith(".webp"):
            return previous
        async with limit:
            try:
                # Unchanged, but not WebP: worth choosing again only if RomM's
                # WebP conversion has made a WebP copy since
                if unchanged and not await _has_webp_copy(romm, rom):
                    return previous
                cover = await _choose(romm, rom, source, folder)
            except RomMError as error:
                # Keep what we had (or nothing) and try again next sync
                log.warning("Could not fetch the cover of %s: %s", rom.fs_name, error)
                return previous
        if cover.file:
            counts["downloaded"] += 1
        return cover

    results = await asyncio.gather(*(one(rom) for rom in roms))
    if counts["downloaded"]:
        log.info("Downloaded %d new or changed covers", counts["downloaded"])
    return [cover for cover in results if cover is not None]


# Replace the covers table. Call inside a transaction.
def write(conn: sqlite3.Connection, covers: list[Cover]) -> None:
    conn.execute("DELETE FROM covers")
    now = int(time.time())
    conn.executemany("INSERT INTO covers (rom_id, source, file, fetched_at) VALUES (?, ?, ?, ?)",
                     [(cover.rom_id, cover.source, cover.file, now) for cover in covers])


# Delete cover files no game uses any more (old versions, removed games)
def remove_unused(cache_path: Path, covers: list[Cover]) -> None:
    in_use = {cover.file for cover in covers if cover.file}
    for path in (cache_path / COVERS_DIR).glob("*"):
        if path.is_file() and path.name not in in_use:
            path.unlink(missing_ok=True)


def _source(rom: RomMRom) -> str:
    return f"{rom.path_cover_small or ''}|{rom.path_cover_large or ''}"


# RomM's WebP conversion saves "small.webp" beside "small.png" and keeps the
# original. Its API still lists the original, and RomM's own web page swaps the
# extension, so the bridge does the same.
def _webp_copy(path: str) -> str | None:
    path = path.split("?", 1)[0]
    stem, dot, extension = path.rpartition(".")
    return f"{stem}.webp" if dot and extension.lower() in ("png", "jpg", "jpeg") else None


# Every cover RomM might have for a game: the small and large ones, and their WebP copies
def _candidates(rom: RomMRom) -> list[str]:
    paths = []
    for path in (rom.path_cover_small, rom.path_cover_large):
        if path:
            paths += [copy for copy in (_webp_copy(path),) if copy] + [path]
    return paths


async def _has_webp_copy(romm: RomMClient, rom: RomMRom) -> bool:
    for path in (rom.path_cover_small, rom.path_cover_large):
        copy = _webp_copy(path) if path else None
        if copy and (await romm.asset_head(copy))[0]:
            return True
    return False


# Try RomM's covers smallest first, and keep the first one tico can show
async def _choose(romm: RomMClient, rom: RomMRom, source: str, folder: Path) -> Cover:
    found = {}
    for path in _candidates(rom):
        available, size = await romm.asset_head(path)
        if available:
            found[path] = size
    # Smallest first; covers whose size RomM did not say go last
    candidates = sorted(found, key=lambda path: (found[path] is None, found[path] or 0))

    for path in candidates:
        data = await romm.asset(path)
        if not data or len(data) > MAX_COVER_BYTES:
            continue
        kind = image_type(data)
        if kind is None:
            continue
        name = f"{rom.id}-{hashlib.sha256(data).hexdigest()[:8]}.{kind}"
        await asyncio.to_thread(_write_file, folder / name, data)
        return Cover(rom.id, source, name)
    return Cover(rom.id, source, None)


# Write a file so readers never see it half-written
def _write_file(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
