from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

import httpx

from ticorom import index, packages
from ticorom.db import Database, transaction
from ticorom.library import GIB, HashCancelled, chd_type, sha256_file, stat_file
from ticorom.romm import RomMClient, RomMError, RomMPlatform, RomMRom
from ticorom.startup.platforms import PlatformMap, Status
from ticorom.startup.settings import Settings

log = logging.getLogger("ticorom.sync")

# Files hashed at the same time. Kept low so hashing does not hog the disk RomM also uses.
HASH_WORKERS = 2

# During a long hash, how often newly hashed games are published.
REPUBLISH_EVERY_SECONDS = 30


# What one sync did. Printed by `ticorom sync` and saved in the sync_runs table.
@dataclass
class SyncResult:
    # Published platforms found in RomM
    platforms: int = 0
    # Games on those platforms
    roms: int = 0
    # Games tico can install now
    published: int = 0
    # Games still waiting for their hash
    pending: int = 0
    # Skip reason -> number of games
    skipped: dict[str, int] = field(default_factory=dict)
    hashed_files: int = 0
    hashed_bytes: int = 0
    hash_failures: int = 0
    seconds: float = 0.0

    # One line, e.g. "618 games on 15 platforms: 613 published, 0 pending, 5 skipped (over_4gib 5)..."
    def summary(self) -> str:
        skipped_total = sum(self.skipped.values())
        reasons = ", ".join(f"{reason} {count}" for reason, count in sorted(self.skipped.items()))

        line = (f"{self.roms} games on {self.platforms} platforms: "
                f"{self.published} published, {self.pending} pending, {skipped_total} skipped")
        if reasons:
            line += f" ({reasons})"
        line += f". Hashed {self.hashed_files} files ({self.hashed_bytes / GIB:.1f} GiB) in {self.seconds:.0f}s"
        if self.hash_failures:
            line += f"; {self.hash_failures} could not be hashed"
        return line + "."


# A file that is new, or whose size or modification time changed since the last sync.
@dataclass
class FileOnDisk:
    # Relative to LIBRARY_PATH, as RomM reports it
    path: str
    size: int
    mtime_ns: int
    # Read from the header of .chd files; None for everything else
    chd_type: str | None


# The result of hashing one file. Either sha256 or problem is set.
@dataclass
class HashOutcome:
    path: str
    size: int
    mtime_ns: int
    sha256: str | None = None
    problem: str | None = None


# Runs syncs. One instance is shared by the background loop and `ticorom sync`.
class Syncer:
    def __init__(self, settings: Settings, platform_map: PlatformMap, database: Database,
                 transport: httpx.AsyncBaseTransport | None = None, hash_workers: int = HASH_WORKERS) -> None:
        self.settings = settings
        self.platform_map = platform_map
        self.database = database
        # Tests pass a fake RomM here; None means the real network
        self.transport = transport
        self.hash_workers = hash_workers

    # Run one sync, start to finish, and record it in the sync_runs table.
    # Setting `stop` makes a running hash give up quickly (used on shutdown).
    async def run(self, stop: threading.Event | None = None) -> SyncResult:
        stop = stop or threading.Event()
        started = time.time()
        run_id = await self.database.run(lambda conn: _start_run(conn, started))
        log.info("Sync started")

        try:
            # Step 1, on the event loop
            platforms, roms = await self._fetch()
            log.info("RomM lists %d games on %d published platforms", len(roms), len(platforms))

            # Steps 2 to 4, in a worker thread
            try:
                result = await asyncio.to_thread(self._look_save_and_hash, platforms, roms, stop, started)
            except asyncio.CancelledError:
                # Cancelling to await does not stop the thread itself. The thread
                # checks this flag while hashing and stops within a second or so.
                stop.set()
                raise

        except BaseException as error:
            self._record_failure(run_id, error)
            raise

        await self.database.run(lambda conn: _finish_run(conn, run_id, "ok", result, None))
        log.info("Sync finished: %s", result.summary())
        return result

    # Mark the run as failed in sync_runs, with a readable reason.
    def _record_failure(self, run_id: int, error: BaseException) -> None:
        if isinstance(error, asyncio.CancelledError):
            message = "cancelled"
        else:
            message = str(error) or type(error).__name__
        # A direct connection rather than database.run(): this must work even
        # while the task is being cancelled.
        with self.database.connection() as conn:
            _finish_run(conn, run_id, "failed", None, message)

    # ----- Step 1: fetch ---------------------------------------------------------

    # Published platforms and all their games. Other platforms are never requested.
    async def _fetch(self) -> tuple[list[RomMPlatform], list[RomMRom]]:
        token = self.settings.romm_api_token.get_secret_value()
        async with RomMClient(self.settings.romm_url, token, self.transport) as romm:
            published = [platform for platform in await romm.platforms()
                         if self.platform_map.status(platform.fs_slug, platform.slug) is Status.PUBLISHED]
            roms: list[RomMRom] = []
            for platform in published:
                roms += await romm.all_roms(platform.id)
        return published, roms

    # ----- Steps 2 to 4 (worker thread) ------------------------------------------

    def _look_save_and_hash(self, platforms: list[RomMPlatform], roms: list[RomMRom],
                            stop: threading.Event, started: float) -> SyncResult:
        result = SyncResult(platforms=len(platforms), roms=len(roms))

        with self.database.connection() as conn:
            # Step 2
            changed, gone = _look_at_disk(conn, self.settings.library_path, roms)
            # Step 3, then write the index files tico reads
            self._save(conn, platforms, roms, changed, gone)
            index.build(conn, self.settings)
            # Step 4
            self._hash_pending(conn, stop, result)
            # Publish whatever was hashed since the last republish
            final_packages = self._publish(conn)

        _count_packages(final_packages, result)
        result.seconds = time.time() - started
        return result

    # ----- Step 3: save ----------------------------------------------------------

    # Write the new state in one transaction: all of it, or none of it.
    def _save(self, conn: sqlite3.Connection, platforms: list[RomMPlatform], roms: list[RomMRom],
              changed: list[FileOnDisk], gone: list[str]) -> None:
        with transaction(conn):
            _update_file_facts(conn, changed, gone)
            _replace_romm_mirror(conn, platforms, roms)
            # Decide what to publish. Files without a hash yet become "pending".
            packages.write(conn, packages.plan(conn, self.platform_map, self.settings))

    # Decide again what to publish now that more files are hashed, and rewrite the index files.
    def _publish(self, conn: sqlite3.Connection) -> list[packages.Package]:
        decided = packages.rebuild(conn, self.platform_map, self.settings)
        index.build(conn, self.settings)
        return decided

    # ----- Step 4: hash ----------------------------------------------------------

    # Hash the files of pending packages, a few at a time, smallest first.
    def _hash_pending(self, conn: sqlite3.Connection, stop: threading.Event, result: SyncResult) -> None:
        todo = conn.execute("""
            SELECT DISTINCT file_facts.path, file_facts.size, file_facts.mtime_ns
            FROM packages JOIN file_facts ON file_facts.path = packages.path
            WHERE packages.status = ?
            ORDER BY file_facts.size, file_facts.path
        """, (packages.PENDING,)).fetchall()
        if not todo:
            return

        total_bytes = sum(row["size"] for row in todo)
        log.info("Hashing %d files (%.1f GiB)", len(todo), total_bytes / GIB)
        library = self.settings.library_path
        last_republish = time.monotonic()

        # The pool hashes files in its own threads. This thread collects the
        # results as they finish and is the only one that writes to the database.
        pool = ThreadPoolExecutor(self.hash_workers, thread_name_prefix="hash")
        try:
            jobs = [pool.submit(_hash_one, library, row["path"], row["size"], row["mtime_ns"], stop)
                    for row in todo]

            for job in as_completed(jobs):
                outcome = job.result()  # raises HashCancelled if `stop` was set

                if outcome.sha256:
                    _save_hash(conn, outcome)
                    result.hashed_files += 1
                    result.hashed_bytes += outcome.size
                    log.info("Successfully hashed %s", outcome.path)
                else:
                    result.hash_failures += 1
                    log.warning("Could not hash %s: %s", outcome.path, outcome.problem)

                # Publish what is ready now, rather than only at the end
                if time.monotonic() - last_republish >= REPUBLISH_EVERY_SECONDS:
                    self._publish(conn)
                    last_republish = time.monotonic()
                    log.info("Hashed %d of %d files (%.1f of %.1f GiB)", result.hashed_files, len(todo),
                             result.hashed_bytes / GIB, total_bytes / GIB)

        except BaseException:
            # Stopping or failing: drop the hashes that have not started yet
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        pool.shutdown(wait=True)


# ----- The background loop used by `ticorom serve` ---------------------------

# Sync now, then every SYNC_INTERVAL_MINUTES, until the app shuts down.
# A failed sync is logged and simply tried again at the next interval.
async def sync_forever(syncer: Syncer, interval_minutes: int, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            await syncer.run(stop)
        except asyncio.CancelledError:
            # The app is shutting down
            raise
        except HashCancelled:
            # Also shutting down: hashing was told to stop
            return
        except (RomMError, OSError, sqlite3.Error) as error:
            # Expected problems (RomM down, library not mounted, ...): one readable line
            log.error("Sync failed: %s. Retrying in %d minutes.", error, interval_minutes)
        except Exception:
            # A bug: log the full traceback
            log.exception("Sync failed unexpectedly. Retrying in %d minutes.", interval_minutes)
        await asyncio.sleep(interval_minutes * 60)


# ----- Helpers for step 2: look at the disk ----------------------------------

# Compare the files on disk with what the database remembers.
# Returns the files that are new or changed (they need hashing), and the
# Paths whose file is gone from disk (their stored hash is useless now).
def _look_at_disk(conn: sqlite3.Connection, library: Path, roms: list[RomMRom]) -> tuple[list[FileOnDisk], list[str]]:
    remembered = {row["path"]: (row["size"], row["mtime_ns"])
                  for row in conn.execute("SELECT path, size, mtime_ns FROM file_facts")}
    listed_by_romm = {file.full_path for rom in roms for file in rom.files}

    changed: list[FileOnDisk] = []
    gone: list[str] = []

    for path in listed_by_romm:
        # (size, mtime_ns), or None if missing
        on_disk = stat_file(library / path)
        if on_disk is None:
            if path in remembered:
                gone.append(path)
        elif on_disk != remembered.get(path):
            size, mtime_ns = on_disk
            kind = chd_type(library / path) if path.lower().endswith(".chd") else None
            changed.append(FileOnDisk(path, size, mtime_ns, kind))

    # Files RomM no longer lists keep their hash for as long as they exist on
    # disk, so re-adding a platform later does not mean hashing it all again.
    for path in remembered:
        if path not in listed_by_romm and stat_file(library / path) is None:
            gone.append(path)

    return changed, gone


# ----- Helpers for step 3: save ----------------------------------------------

# Forget files gone from disk; record new and changed ones with no hash yet.
def _update_file_facts(conn: sqlite3.Connection, changed: list[FileOnDisk], gone: list[str]) -> None:
    conn.executemany("DELETE FROM file_facts WHERE path = ?", [(path,) for path in gone])
    conn.executemany("""
        INSERT INTO file_facts (path, size, mtime_ns, sha256, chd_type, hashed_at)
        VALUES (?, ?, ?, NULL, ?, NULL)
        ON CONFLICT (path) DO UPDATE SET
            size = excluded.size, mtime_ns = excluded.mtime_ns, chd_type = excluded.chd_type,
            sha256 = NULL, hashed_at = NULL
    """, [(file.path, file.size, file.mtime_ns, file.chd_type) for file in changed])


# Throw away the old copy of RomM's data and insert the new one.
def _replace_romm_mirror(conn: sqlite3.Connection, platforms: list[RomMPlatform], roms: list[RomMRom]) -> None:
    for table in ("packages", "rom_files", "roms", "platforms"):
        conn.execute(f"DELETE FROM {table}")

    conn.executemany("INSERT INTO platforms (id, slug, fs_slug, name) VALUES (?, ?, ?, ?)",
                     [(p.id, p.slug, p.fs_slug, p.name or p.fs_slug) for p in platforms])
    conn.executemany("""
        INSERT INTO roms (id, platform_id, name, fs_name, summary, genres, release_date, cover_path, romm_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [_rom_row(rom) for rom in roms])
    conn.executemany("""
        INSERT INTO rom_files (id, rom_id, path, file_name, is_top_level, category, archive_members)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, _file_rows(roms))


# One row of the roms table.
def _rom_row(rom: RomMRom) -> tuple:
    metadata = rom.metadatum
    genres = metadata.genres if metadata else []
    release_ms = metadata.first_release_date if metadata else None   # RomM gives milliseconds
    return (
        rom.id,
        rom.platform_id,
        rom.name or PurePosixPath(rom.fs_name).stem,    # no name from metadata: use the file name
        rom.fs_name,
        rom.summary,
        json.dumps(genres),
        release_ms // 1000 if release_ms else None,     # stored as seconds
        rom.path_cover_small,
        rom.updated_at,
    )


# Rows of the rom_files table. A path RomM lists twice is kept once.
def _file_rows(roms: list[RomMRom]) -> list[tuple]:
    rows, seen = [], set()
    for rom in roms:
        for file in rom.files:
            if file.full_path in seen:
                log.warning("RomM lists %s under more than one game; keeping the first", file.full_path)
                continue
            seen.add(file.full_path)
            archive_members = json.dumps(file.archive_members) if file.archive_members is not None else None
            rows.append((file.id, rom.id, file.full_path, file.file_name,
                         int(file.is_top_level), file.category, archive_members))
    return rows


# ----- Helpers for step 4: hash ----------------------------------------------

# Hash one file. Runs in a pool thread.
# The file is checked before and after hashing: if it changed in between,
# the hash would describe neither version, so it is thrown away and the file is hashed again next sync.
def _hash_one(library: Path, path: str, size: int, mtime_ns: int, stop: threading.Event) -> HashOutcome:
    outcome = HashOutcome(path, size, mtime_ns)
    full_path = library / path
    try:
        if stat_file(full_path) != (size, mtime_ns):
            outcome.problem = "the file changed during the sync; it is hashed next time"
            return outcome
        sha256 = sha256_file(full_path, stop)       # raises HashCancelled if `stop` is set
        if stat_file(full_path) != (size, mtime_ns):
            outcome.problem = "the file changed while it was hashed; it is hashed next time"
            return outcome
    except OSError as error:
        outcome.problem = error.strerror or str(error)
        return outcome
    outcome.sha256 = sha256
    return outcome


# Store a hash, unless the file's facts changed in the database meanwhile.
def _save_hash(conn: sqlite3.Connection, outcome: HashOutcome) -> None:
    conn.execute("""
        UPDATE file_facts SET sha256 = ?, hashed_at = ?
        WHERE path = ? AND size = ? AND mtime_ns = ?
    """, (outcome.sha256, int(time.time()), outcome.path, outcome.size, outcome.mtime_ns))


# Fill in the published / pending / skipped totals.
def _count_packages(final_packages: list[packages.Package], result: SyncResult) -> None:
    statuses = Counter(package.status for package in final_packages)
    result.published = statuses[packages.PUBLISHED]
    result.pending = statuses[packages.PENDING]
    result.skipped = dict(Counter(package.skip_reason for package in final_packages
                                  if package.status == packages.SKIPPED))


# ----- Helpers for the sync_runs table ---------------------------------------

# Add a "running" row and return its id.
def _start_run(conn: sqlite3.Connection, started: float) -> int:
    with transaction(conn):
        cursor = conn.execute("INSERT INTO sync_runs (started_at, status) VALUES (?, 'running')", (int(started),))
    return cursor.lastrowid


# Mark the run "ok" (with its numbers) or "failed" (with the reason).
def _finish_run(conn: sqlite3.Connection, run_id: int, status: str,
                result: SyncResult | None, error: str | None) -> None:
    stats = json.dumps(asdict(result)) if result else None
    with transaction(conn):
        conn.execute("UPDATE sync_runs SET finished_at = ?, status = ?, stats = ?, error = ? WHERE id = ?",
                     (int(time.time()), status, stats, error, run_id))
