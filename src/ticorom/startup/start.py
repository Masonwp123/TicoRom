from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from pydantic import ValidationError

import uvicorn

from ticorom import __version__
from ticorom.app import create_app
from ticorom.db import Database, DatabaseError
from ticorom.startup.preflight import run_preflight
from ticorom.sync import Syncer
from ticorom.startup.settings import Settings, describe_errors, load_settings
from ticorom.startup.platforms import PlatformMap, PlatformMapError, load_platform_map

# Get project settings or exit the program with errors
def _settings_or_exit() -> Settings:
    try:
        print(f"TicoRom {__version__} loading settings:")
        settings = load_settings()
        print(f"  OK    Successfully loaded settings.")
        return settings
    except ValidationError as error:
        print("Configuration problems:")
        for line in describe_errors(error):
            print(f"  FAIL  {line}")
        sys.exit(1)

def _platform_map_or_defaults(settings: Settings) -> PlatformMap:
    try:
        print(f"TicoRom {__version__} loading platform map:")
        platform_map, created = load_platform_map(settings.config_path)
        print(f"  OK    Successfully loaded platform map.")
        return platform_map
    except PlatformMapError as error:
        print(f"  FAIL  {error}")
        sys.exit(1)

def _preflight_or_exit(settings: Settings, platform_map: PlatformMap):
    print(f"TicoRom {__version__} startup checks:")
    report = asyncio.run(run_preflight(settings, platform_map))
    print("\n".join(report.lines()), flush=True)
    if not report.passed:
        print("Startup checks failed.")
        sys.exit(1)
    print("  OK    Startup checks passed.")

# Open the database and bring its schema up to date, or exit with errors
def _database_or_exit(settings: Settings) -> Database:
    print(f"TicoRom {__version__} opening the database:")
    database = Database.in_config(settings.config_path)
    try:
        version = database.migrate()
    except DatabaseError as error:
        print(f"  FAIL  {error}")
        sys.exit(1)
    print(f"  OK    {database.path} is at schema version {version}.")
    return database

# Run one sync in the foreground and print what it did
def _sync_once(syncer: Syncer) -> None:
    print(f"TicoRom {__version__} syncing with RomM:", flush=True)
    try:
        result = asyncio.run(syncer.run())
    except KeyboardInterrupt:
        print("Sync stopped. Files hashed so far are kept.")
        sys.exit(130)
    except Exception as error:
        print(f"  FAIL  Sync failed: {error}")
        sys.exit(1)
    print(f"  OK    {result.summary()}")

# Create commands and arguments used for said commands
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ticorom")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("check", help="run the startup checks and exit")

    commands.add_parser("sync", help="run the startup checks, then sync with RomM once and exit")

    serve = commands.add_parser("serve", help="run the startup checks, then start the bridge")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)

    args = parser.parse_args()

    return args

def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per RomM request is noise

    # Get the settings for the app and run the preflight checks
    settings = _settings_or_exit()
    platform_map = _platform_map_or_defaults(settings)
    _preflight_or_exit(settings, platform_map)
    database = _database_or_exit(settings)

    # Check doesn't run the bridge
    if args.command == "check":
        return

    syncer = Syncer(settings, platform_map, database)
    if args.command == "sync":
        _sync_once(syncer)
        return

    print(f"Starting TicoRom {__version__}:")

    uvicorn.run(
        create_app(settings, platform_map, syncer), host=args.host, port=args.port, log_level="info"
    )
