from __future__ import annotations

import gzip
import json
import os
import re
import sqlite3
import tempfile
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from ticorom.packages import PUBLISHED
from ticorom.startup.platforms import TICO_SLUG_RE
from ticorom.startup.settings import Settings

INDEX_DIR = "index"
# File name of the combined index; "_" never appears in a console name
ALL_CONSOLES = "_all"
TOKEN_PLACEHOLDER = "@@TICOROM_TOKEN@@"
# Characters of RomM's summary shown in tico
DESCRIPTION_LIMIT = 600
FILE_NAME_TAGS = re.compile(r"[(\[]([^)\]]+)[)\]]")
GZIP_CACHE_SIZE = 32


# ----- Building ----------------------------------------------------------------

# Write the combined and per-console index files. Returns the number of games published.
def build(conn: sqlite3.Connection, settings: Settings) -> int:
    rows = conn.execute("""
        SELECT packages.id, packages.tico_slug, packages.install, packages.filename, packages.version,
               roms.name, roms.fs_name, roms.summary, roms.genres, roms.release_date,
               file_facts.size, file_facts.sha256, platforms.name AS platform_name,
               covers.file AS cover_file
        FROM packages
        JOIN roms ON roms.id = packages.rom_id
        JOIN platforms ON platforms.id = roms.platform_id
        JOIN file_facts ON file_facts.path = packages.path
        LEFT JOIN covers ON covers.rom_id = roms.id
        WHERE packages.status = ? AND file_facts.sha256 IS NOT NULL
        ORDER BY roms.name COLLATE NOCASE, packages.id
    """, (PUBLISHED,)).fetchall()

    everything: list[dict] = []
    by_console: dict[str, list[dict]] = {}
    console_names: dict[str, str] = {}
    for row in rows:
        entry = _package_entry(row, settings.app_url)
        everything.append(entry)
        by_console.setdefault(row["tico_slug"], []).append(entry)
        console_names.setdefault(row["tico_slug"], row["platform_name"])

    folder = settings.cache_path / INDEX_DIR
    folder.mkdir(parents=True, exist_ok=True)
    title = repository_title(settings)
    _write_json(folder / f"{ALL_CONSOLES}.json", _repository(title, everything))
    for slug, entries in by_console.items():
        _write_json(folder / f"{slug}.json", _repository(f"{title}: {console_names[slug]}", entries))

    # A console with no published games left has no index anymore
    for stale in folder.glob("*.json"):
        if stale.stem != ALL_CONSOLES and stale.stem not in by_console:
            stale.unlink(missing_ok=True)
    return len(everything)


# The name tico shows on the repository's Sources card
def repository_title(settings: Settings) -> str:
    return f"TicoRom ({settings.instance_slug})"


def _repository(name: str, packages: list[dict]) -> dict:
    return {"schema": 1, "name": name, "description": "Games from RomM, published by TicoRom.", "packages": packages}


# One package, with the fields from DESIGN.md section 7.3
def _package_entry(row: sqlite3.Row, app_url: str) -> dict:
    url = (f"{app_url}/r/{TOKEN_PLACEHOLDER}/dl/{row['id']}/{row['version']}/"
           f"{quote(row['filename'], safe='')}")
    entry = {"id": row["id"], "name": row["name"]}
    description = _shorten(row["summary"] or "", DESCRIPTION_LIMIT)
    if description:
        entry["description"] = description
    line = tagline(row["fs_name"], json.loads(row["genres"]), row["release_date"])
    if line:
        entry["tagline"] = line
    if row["cover_file"]:
        entry["icon"] = f"{app_url}/r/{TOKEN_PLACEHOLDER}/img/{row['cover_file']}"
    entry.update({
        "category": "game",
        "tags": [row["tico_slug"]],
        "version": row["version"],
        "type": "zip",              # file mode: delivered as-is (DESIGN.md section 7.1)
        "extract": False,
        "url": url,
        "filename": row["filename"],
        "install": row["install"],
        "size": row["size"],
        "sha256": row["sha256"],
    })
    return entry


# The tags in the file name ("USA · Rev 1 · Disc 2"), so copies of a game can
# be told apart; otherwise the first genre and the release year.
def tagline(fs_name: str, genres: list[str], release_date: int | None) -> str:
    tags = [tag.strip() for tag in FILE_NAME_TAGS.findall(PurePosixPath(fs_name).stem) if tag.strip()]
    if tags:
        return " · ".join(tags)
    parts = genres[:1]
    if release_date:
        parts.append(str(datetime.fromtimestamp(release_date, timezone.utc).year))
    return " · ".join(parts)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


# Write a file so readers never see it half-written: write a temporary file, then rename it
def _write_json(path: Path, data: dict) -> None:
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, separators=(",", ":"))
        os.chmod(temporary, 0o644)  # temporary files start as owner-only
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# ----- Serving -----------------------------------------------------------------

# Reads the built index files and fills in the token for one request.
class IndexFiles:

    def __init__(self, settings: Settings) -> None:
        self.folder = settings.cache_path / INDEX_DIR
        self.title = repository_title(settings)
        self._gzipped: OrderedDict[tuple, bytes] = OrderedDict()
        self._lock = threading.Lock()

    # Consoles that have an index
    def consoles(self) -> list[str]:
        return sorted(path.stem for path in self.folder.glob("*.json") if path.stem != ALL_CONSOLES)

    # The index for a token: all its consoles (console=None) or one console.
    # None if that console does not exist or the token may not see it.
    def render(self, token: str, allowed: list[str] | None, console: str | None, gz: bool) -> bytes | None:
        if console is not None:
            if not TICO_SLUG_RE.fullmatch(console) or (allowed is not None and console not in allowed):
                return None
            sources = [self.folder / f"{console}.json"]
            if not sources[0].is_file():
                return None
        elif allowed is None:
            sources = [self.folder / f"{ALL_CONSOLES}.json"]
        else:
            sources = [self.folder / f"{slug}.json" for slug in sorted(allowed)]

        versions = tuple(_mtime(path) for path in sources)
        key = (token, console, tuple(allowed or ()), versions)
        if gz:
            with self._lock:
                if key in self._gzipped:
                    self._gzipped.move_to_end(key)
                    return self._gzipped[key]

        body = self._body(sources, console is None and allowed is not None)
        body = body.replace(f"/r/{TOKEN_PLACEHOLDER}/".encode(), f"/r/{token}/".encode())
        if not gz:
            return body

        packed = gzip.compress(body, compresslevel=6, mtime=0)
        with self._lock:
            self._gzipped[key] = packed
            while len(self._gzipped) > GZIP_CACHE_SIZE:
                self._gzipped.popitem(last=False)
        return packed

    def _body(self, sources: list[Path], merge: bool) -> bytes:
        if not merge:
            try:
                return sources[0].read_bytes()
            except FileNotFoundError:
                # No sync has finished yet: an empty repository, so tico can still add it
                return json.dumps(_repository(self.title, [])).encode()
        # A token limited to some consoles: combine their indexes
        packages: list[dict] = []
        for path in sources:
            try:
                packages += json.loads(path.read_bytes())["packages"]
            except FileNotFoundError:
                continue
        packages.sort(key=lambda entry: (entry["name"].casefold(), entry["id"]))
        return json.dumps(_repository(self.title, packages), ensure_ascii=False, separators=(",", ":")).encode()


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0
