from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import astuple, dataclass
from pathlib import PurePosixPath

from ticorom.db import transaction
from ticorom.library import FAT32_LIMIT
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
def decide(rom_id: int, files: list[GameFile], entry: Platform, platform_map: PlatformMap,
           facts: dict[str, FileFact], settings: Settings, fallback_path: str) -> Package:
    games = [file for file in files if file.category in (None, "game")]
    file = games[0] if len(games) == 1 else None

    def package(status: str, reason: str | None = None, version: str | None = None) -> Package:
        path = file.path if file else fallback_path
        name = file.file_name if file else PurePosixPath(fallback_path).name
        return Package(
            id=package_id(settings.instance_slug, rom_id), rom_id=rom_id, path=path,
            tico_slug=entry.tico_slug, install=entry.install,
            filename=safe_filename(name, f"rom-{rom_id}{PurePosixPath(name).suffix}"),
            mode="file", version=version, status=status, skip_reason=reason,
        )

    if file is None:
        return package(SKIPPED, SKIP_MULTI_FILE)
    fact = facts.get(file.path)
    if fact is None:
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


# Every game in the RomM mirror, with what should happen to it.
def plan(conn: sqlite3.Connection, platform_map: PlatformMap, settings: Settings) -> list[Package]:
    facts = {
        row["path"]: FileFact(row["size"], row["sha256"], row["chd_type"])
        for row in conn.execute("SELECT path, size, sha256, chd_type FROM file_facts")
    }
    files: dict[int, list[GameFile]] = defaultdict(list)
    for row in conn.execute("SELECT rom_id, path, file_name, category FROM rom_files ORDER BY id"):
        files[row["rom_id"]].append(GameFile(row["path"], row["file_name"], row["category"]))

    packages = []
    for row in conn.execute("""
        SELECT roms.id, roms.fs_name, platforms.slug, platforms.fs_slug
        FROM roms JOIN platforms ON platforms.id = roms.platform_id
        ORDER BY roms.id
    """):
        entry = platform_map.entry(row["fs_slug"], row["slug"])
        if entry is None or not entry.enabled:
            continue
        fallback = files[row["id"]][0].path if files[row["id"]] else row["fs_name"]
        packages.append(decide(row["id"], files[row["id"]], entry, platform_map, facts, settings, fallback))
    return packages


# Replace the packages table. Call inside a transaction.
def write(conn: sqlite3.Connection, packages: list[Package]) -> None:
    conn.execute("DELETE FROM packages")
    conn.executemany("INSERT INTO packages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     [astuple(package) for package in packages])


def rebuild(conn: sqlite3.Connection, platform_map: PlatformMap, settings: Settings) -> list[Package]:
    with transaction(conn):
        packages = plan(conn, platform_map, settings)
        write(conn, packages)
    return packages
