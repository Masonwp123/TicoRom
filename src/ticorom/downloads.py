from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from starlette.responses import FileResponse

from ticorom.library import is_safe_relative
from ticorom.packages import PUBLISHED


@dataclass(frozen=True)
class Download:
    path: str          # relative to LIBRARY_PATH
    filename: str
    tico_slug: str
    size: int          # recorded when the file was hashed
    mtime_ns: int


# A file from the library. Read in 1 MiB pieces instead of 64 KiB: fewer trips to the disk.
class LibraryFile(FileResponse):
    chunk_size = 1024 * 1024


# The published package behind a download address, or None
def find(conn: sqlite3.Connection, package_id: str, version: str) -> Download | None:
    row = conn.execute("""
        SELECT packages.path, packages.filename, packages.tico_slug, file_facts.size, file_facts.mtime_ns
        FROM packages JOIN file_facts ON file_facts.path = packages.path
        WHERE packages.id = ? AND packages.version = ? AND packages.status = ?
    """, (package_id, version, PUBLISHED)).fetchone()
    return Download(row["path"], row["filename"], row["tico_slug"], row["size"], row["mtime_ns"]) if row else None


# The file's current stat if it is still exactly the file that was hashed, else None
def unchanged_stat(library: Path, download: Download) -> os.stat_result | None:
    # Never serve anything outside the library, whatever the database says
    if not is_safe_relative(download.path):
        return None
    try:
        info = os.stat(library / download.path)
    except OSError:
        return None
    if (info.st_size, info.st_mtime_ns) != (download.size, download.mtime_ns):
        return None
    return info
