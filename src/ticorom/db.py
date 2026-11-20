from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

DB_FILE_NAME = "ticorom.db"
T = TypeVar("T")

# Each entry is one migration. The database's PRAGMA user_version is the number
# of migrations applied, so entries must never be edited or reordered once released.
MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE platforms (
      id              INTEGER PRIMARY KEY,
      slug            TEXT NOT NULL,
      fs_slug         TEXT NOT NULL,
      name            TEXT NOT NULL
    );

    CREATE TABLE roms (
      id              INTEGER PRIMARY KEY,
      platform_id     INTEGER NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
      name            TEXT NOT NULL,
      fs_name         TEXT NOT NULL,
      summary         TEXT,
      genres          TEXT NOT NULL DEFAULT '[]',
      release_date    INTEGER,
      cover_path      TEXT,
      romm_updated_at TEXT NOT NULL
    );

    CREATE TABLE rom_files (
      id              INTEGER PRIMARY KEY,
      rom_id          INTEGER NOT NULL REFERENCES roms(id) ON DELETE CASCADE,
      path            TEXT NOT NULL UNIQUE,
      file_name       TEXT NOT NULL,
      is_top_level    INTEGER NOT NULL,
      category        TEXT,
      archive_members TEXT
    );
    CREATE INDEX rom_files_by_rom ON rom_files(rom_id);

    CREATE TABLE file_facts (
      path            TEXT PRIMARY KEY,
      size            INTEGER NOT NULL,
      mtime_ns        INTEGER NOT NULL,
      sha256          TEXT,
      chd_type        TEXT,
      hashed_at       INTEGER
    );

    CREATE TABLE packages (
      id              TEXT PRIMARY KEY,
      rom_id          INTEGER NOT NULL REFERENCES roms(id) ON DELETE CASCADE,
      path            TEXT NOT NULL,
      tico_slug       TEXT NOT NULL,
      install         TEXT NOT NULL,
      filename        TEXT NOT NULL,
      mode            TEXT NOT NULL,
      version         TEXT,
      status          TEXT NOT NULL,
      skip_reason     TEXT
    );
    CREATE INDEX packages_by_console ON packages(tico_slug, status);

    CREATE TABLE sync_runs (
      id              INTEGER PRIMARY KEY,
      started_at      INTEGER NOT NULL,
      finished_at     INTEGER,
      status          TEXT NOT NULL,
      stats           TEXT,
      error           TEXT
    );

    CREATE TABLE tokens (
      id              INTEGER PRIMARY KEY,
      name            TEXT NOT NULL UNIQUE,
      token_sha256    TEXT NOT NULL UNIQUE,
      platforms       TEXT,
      created_at      INTEGER NOT NULL,
      revoked_at      INTEGER,
      last_used_at    INTEGER
    );
    """,
]


class DatabaseError(Exception):
    """The database cannot be opened or migrated. The message is ready to show."""


# Open a connection. Transactions are explicit: use transaction().
def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Run a block as one write transaction; roll back if it raises.
@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# Create or upgrade the database. Returns the schema version.
def migrate(path: Path) -> int:
    try:
        conn = connect(path)
    except sqlite3.Error as error:
        raise DatabaseError(f"cannot open the database {path}: {error}") from error
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise DatabaseError(f"the database {path} is at schema version {version}, which is newer than "
                                f"this TicoRom understands ({len(MIGRATIONS)}). Was TicoRom downgraded?")
        for number in range(version + 1, len(MIGRATIONS) + 1):
            # executescript commits any open transaction, so the script carries its own
            script = f"BEGIN;\n{MIGRATIONS[number - 1]}\nPRAGMA user_version = {number};\nCOMMIT;"
            try:
                conn.executescript(script)
            except sqlite3.Error as error:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise DatabaseError(f"migration {number} of {path} failed: {error}") from error
        return len(MIGRATIONS)
    except sqlite3.Error as error:
        raise DatabaseError(f"cannot use the database {path}: {error}") from error
    finally:
        conn.close()


# Where the database lives, and a way to use it from async code.
class Database:

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def in_config(cls, config_path: Path) -> Database:
        return cls(config_path / DB_FILE_NAME)

    def migrate(self) -> int:
        return migrate(self.path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path)
        try:
            yield conn
        finally:
            conn.close()

    # Run work(conn) in a worker thread with its own connection.
    async def run(self, work: Callable[[sqlite3.Connection], T]) -> T:
        def call() -> T:
            with self.connection() as conn:
                return work(conn)
        return await asyncio.to_thread(call)
