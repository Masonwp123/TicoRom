from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import astuple, dataclass, replace
from pathlib import PurePosixPath

from ticorom.db import transaction
from ticorom.library import FAT32_LIMIT, is_safe_relative
from ticorom.startup.platforms import Platform, PlatformMap
from ticorom.startup.settings import Settings

PUBLISHED, PENDING, SKIPPED = "published", "pending", "skipped"

# Why a game is not published. Shown in the skipped-games report.

# Needs bundle mode, which does not exist yet
SKIP_MULTI_FILE = "multi_file"
# RomM lists it, but the file is not in the library folder
SKIP_MISSING = "missing"
# Not an extension the console's emulator loads
SKIP_EXTENSION = "extension"
# Platform wants archives unpacked; extract mode does not exist yet
SKIP_EXTRACT_MODE = "extract_mode"
# FAT32 cannot hold it (SKIP_FILES_OVER_4GIB)
SKIP_OVER_4GIB = "over_4gib"
# A CHD variant the console's emulator does not launch
SKIP_CHD_TYPE = "chd_type"
# RomM lists the game without any file
SKIP_NO_FILE = "no_file"
# RomM gave a path that leads outside the library ("..", or absolute)
SKIP_UNSAFE_PATH = "unsafe_path"

ARCHIVES = {"zip", "7z", "rar"}
MAX_FILENAME = 200
UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f\\/:*?"<>|]')


@dataclass(frozen=True)
class FileFact:
    size: int
    sha256: str | None
    chd_type: str | None


@dataclass(frozen=True)
class GameFile:
    path: str
    file_name: str
    category: str | None
    is_top_level: bool = True


@dataclass(frozen=True)
class Package:
    id: str
    rom_id: int
    path: str
    tico_slug: str
    install: str
    filename: str
    mode: str
    version: str | None
    status: str
    skip_reason: str | None


def package_id(instance_slug: str, rom_id: int) -> str:
    return f"romm-{instance_slug}-{rom_id}"


# A plain file name a FAT32 or exFAT card can hold (DESIGN.md section 9, "File names").
def safe_filename(name: str, fallback: str) -> str:
    name = UNSAFE_FILENAME_CHARS.sub("", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if not name:
        return fallback
    if len(name) > MAX_FILENAME:
        suffix = PurePosixPath(name).suffix
        name = name[: MAX_FILENAME - len(suffix)].rstrip(". ") + suffix
    return name


def extension(file_name: str) -> str:
    return PurePosixPath(file_name).suffix.lower().lstrip(".")


# The package for one game, published or not.
# `missing` holds the paths RomM lists that are not on disk right now. Their
# hashes are kept (DESIGN.md section 6.8), so they need telling apart here.
def decide(rom_id: int, rom_fs_name: str, files: list[GameFile], entry: Platform, platform_map: PlatformMap,
           facts: dict[str, FileFact], settings: Settings, missing: frozenset[str] = frozenset()) -> Package:
    games = [file for file in files if file.category in (None, "game")]
    file = games[0] if len(games) == 1 else None

    def package(status: str, reason: str | None = None, version: str | None = None) -> Package:
        path = file.path if file else (files[0].path if files else rom_fs_name)
        return Package(
            id=package_id(settings.instance_slug, rom_id), rom_id=rom_id, path=path,
            tico_slug=entry.tico_slug, install=entry.install,
            filename=_filename(rom_id, rom_fs_name, file),
            mode="file", version=version, status=status, skip_reason=reason,
        )

    if not files:
        return package(SKIPPED, SKIP_NO_FILE)
    if file is None:
        return package(SKIPPED, SKIP_MULTI_FILE)
    if not is_safe_relative(file.path):
        return package(SKIPPED, SKIP_UNSAFE_PATH)
    fact = facts.get(file.path)
    if fact is None or file.path in missing:
        return package(SKIPPED, SKIP_MISSING)
    ext = extension(file.file_name)
    if ext not in entry.extensions:
        return package(SKIPPED, SKIP_EXTENSION)
    zip_policy = entry.zip_policy or platform_map.defaults.zip_policy
    if ext in ARCHIVES and zip_policy == "extract":
        return package(SKIPPED, SKIP_EXTRACT_MODE)
    if settings.skip_files_over_4gib and fact.size >= FAT32_LIMIT:
        return package(SKIPPED, SKIP_OVER_4GIB)
    if ext == "chd" and entry.chd_types is not None and fact.chd_type not in entry.chd_types:
        return package(SKIPPED, SKIP_CHD_TYPE)
    if fact.sha256 is None:
        return package(PENDING)
    return package(PUBLISHED, version=fact.sha256[:8])


# The name the file gets on the SD card. A file inside a game's folder (e.g.
# "Game A/disc.chd") takes the folder's name ("Game A.chd"): many games use the
# same inner file name, and they all install into one console folder.
# RomM's is_top_level does not tell these apart (it is also true for a file
# directly inside the game's folder), so compare names: a game stored as a single
# file has the same name as that file.
def _filename(rom_id: int, rom_fs_name: str, file: GameFile | None) -> str:
    rom_name = PurePosixPath(rom_fs_name).name
    if file is None:
        name = rom_name
    elif file.file_name == rom_name:
        name = file.file_name
    else:
        name = rom_name + PurePosixPath(file.file_name).suffix
    return safe_filename(name, f"rom-{rom_id}{PurePosixPath(name).suffix}")


# Two games must never install to the same file. SD cards are FAT32 or exFAT,
# which ignore case, so "Quest.7z" and "QUEST.7z" are the same file there.
# The game with the lowest RomM id keeps its name; the others get their id added.
def _resolve_name_clashes(packages: list[Package]) -> list[Package]:
    taken: set[tuple[str, str]] = set()
    result = []
    for package in sorted(packages, key=lambda p: p.rom_id):
        if package.status != SKIPPED:
            key = (package.install.casefold(), package.filename.casefold())
            if key in taken:
                name = PurePosixPath(package.filename)
                package = replace(package, filename=safe_filename(f"{name.stem} ({package.rom_id}){name.suffix}",
                                                                  package.filename))
                key = (package.install.casefold(), package.filename.casefold())
            taken.add(key)
        result.append(package)
    return result


# Every game in the RomM mirror, with what should happen to it.
def plan(conn: sqlite3.Connection, platform_map: PlatformMap, settings: Settings,
         missing: frozenset[str] = frozenset()) -> list[Package]:
    facts = {
        row["path"]: FileFact(row["size"], row["sha256"], row["chd_type"])
        for row in conn.execute("SELECT path, size, sha256, chd_type FROM file_facts")
    }
    files: dict[int, list[GameFile]] = defaultdict(list)
    for row in conn.execute("SELECT rom_id, path, file_name, category, is_top_level FROM rom_files ORDER BY id"):
        files[row["rom_id"]].append(GameFile(row["path"], row["file_name"], row["category"], bool(row["is_top_level"])))

    packages = []
    for row in conn.execute("""
        SELECT roms.id, roms.fs_name, platforms.slug, platforms.fs_slug
        FROM roms JOIN platforms ON platforms.id = roms.platform_id
        ORDER BY roms.id
    """):
        entry = platform_map.entry(row["fs_slug"], row["slug"])
        if entry is None or not entry.enabled:
            continue
        packages.append(decide(row["id"], row["fs_name"], files[row["id"]], entry, platform_map, facts, settings,
                               missing))
    return _resolve_name_clashes(packages)


# Replace the packages table. Call inside a transaction.
def write(conn: sqlite3.Connection, packages: list[Package]) -> None:
    conn.execute("DELETE FROM packages")
    conn.executemany("INSERT INTO packages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     [astuple(package) for package in packages])


def rebuild(conn: sqlite3.Connection, platform_map: PlatformMap, settings: Settings,
            missing: frozenset[str] = frozenset()) -> list[Package]:
    with transaction(conn):
        packages = plan(conn, platform_map, settings, missing)
        write(conn, packages)
    return packages
