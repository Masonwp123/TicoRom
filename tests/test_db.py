import asyncio
import sqlite3

import pytest

from ticorom.db import MIGRATIONS, Database, DatabaseError, connect, migrate, transaction

TABLES = {"platforms", "roms", "rom_files", "file_facts", "packages", "sync_runs", "tokens", "covers"}


def test_migrate_creates_the_schema(tmp_path):
    path = tmp_path / "ticorom.db"
    assert migrate(path) == len(MIGRATIONS)
    conn = connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert TABLES <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_migrate_is_safe_to_run_again(tmp_path):
    path = tmp_path / "ticorom.db"
    migrate(path)
    conn = connect(path)
    conn.execute("INSERT INTO platforms VALUES (1, 'gba', 'gba', 'Game Boy Advance')")
    conn.close()
    migrate(path)
    assert connect(path).execute("SELECT count(*) FROM platforms").fetchone()[0] == 1


def test_a_newer_database_is_refused(tmp_path):
    path = tmp_path / "ticorom.db"
    migrate(path)
    connect(path).execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    with pytest.raises(DatabaseError, match="newer than this TicoRom"):
        migrate(path)


def test_a_failed_transaction_changes_nothing(tmp_path):
    path = tmp_path / "ticorom.db"
    migrate(path)
    conn = connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            conn.execute("INSERT INTO platforms VALUES (1, 'gba', 'gba', 'GBA')")
            conn.execute("INSERT INTO platforms VALUES (1, 'gba', 'gba', 'duplicate id')")
    assert conn.execute("SELECT count(*) FROM platforms").fetchone()[0] == 0


def test_deleting_a_platform_removes_its_games(tmp_path):
    path = tmp_path / "ticorom.db"
    migrate(path)
    conn = connect(path)
    conn.execute("INSERT INTO platforms VALUES (1, 'gba', 'gba', 'GBA')")
    conn.execute("INSERT INTO roms (id, platform_id, name, fs_name, romm_updated_at) VALUES (5, 1, 'Game', 'Game.gba', '')")
    conn.execute("INSERT INTO rom_files (id, rom_id, path, file_name, is_top_level) VALUES (9, 5, 'roms/gba/Game.gba', 'Game.gba', 1)")
    conn.execute("DELETE FROM platforms")
    assert conn.execute("SELECT count(*) FROM rom_files").fetchone()[0] == 0


def test_run_uses_a_worker_thread_connection(tmp_path):
    database = Database.in_config(tmp_path)
    database.migrate()
    count = asyncio.run(database.run(lambda conn: conn.execute("SELECT count(*) FROM tokens").fetchone()[0]))
    assert count == 0
    assert database.path == tmp_path / "ticorom.db"


def test_migration_2_adds_covers_to_an_existing_database(tmp_path):
    path = tmp_path / "ticorom.db"
    conn = connect(path)
    conn.executescript(f"BEGIN; {MIGRATIONS[0]} PRAGMA user_version = 1; COMMIT;")   # a version 1 database
    conn.execute("INSERT INTO platforms VALUES (1, 'gba', 'gba', 'GBA')")
    conn.close()
    assert migrate(path) == 2
    conn = connect(path)
    assert "cover_large_path" in [row[1] for row in conn.execute("PRAGMA table_info(roms)")]
    assert conn.execute("SELECT count(*) FROM platforms").fetchone()[0] == 1
