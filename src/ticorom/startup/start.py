from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from pydantic import ValidationError

import uvicorn

from ticorom import __version__, tokens
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
    with database.connection() as conn:
        if tokens.count_active(conn) == 0:
            print("  WARN  There are no access tokens yet, so tico cannot reach anything. Create one with\n"
                  "        `ticorom token add <name>` (in Docker: `docker compose run --rm ticorom token add <name>`).")
    return database

# The tico console names that are published, for token commands
def _published_consoles(platform_map: PlatformMap) -> set[str]:
    return {entry.tico_slug for entry in platform_map.platforms.values() if entry.enabled}

# `ticorom token add | list | revoke`
def _token_command(args: argparse.Namespace, settings: Settings, platform_map: PlatformMap,
                   database: Database) -> None:
    try:
        with database.connection() as conn:
            if args.token_command == "add":
                _token_add(conn, args, settings, platform_map)
            elif args.token_command == "list":
                _token_list(conn)
            else:
                tokens.revoke(conn, args.name)
                print(f'Revoked the token "{args.name}". tico can no longer use any address that contains it.')
    except tokens.TokenError as error:
        print(f"  FAIL  {error}")
        sys.exit(1)

def _token_add(conn, args: argparse.Namespace, settings: Settings, platform_map: PlatformMap) -> None:
    published = _published_consoles(platform_map)
    scope = [name.strip() for name in args.platforms.split(",") if name.strip()] if args.platforms else None
    token = tokens.add(conn, args.name, scope, published)
    base = f"{settings.app_url}/r/{token}"
    consoles = ", ".join(sorted(scope or published))
    print(f'\nCreated the token "{args.name.strip()}". It is shown only this once: treat it like a password.\n')
    print(f"  Token:  {token}\n")
    print("Add one of these in tico (Store > Sources > Add repository):")
    print(f"  All consoles:  {base}/index.json.gz")
    print(f"  One console:   {base}/<console>/index.json.gz")
    print(f"                 where <console> is one of: {consoles}\n")

def _token_list(conn) -> None:
    found = tokens.list_tokens(conn)
    if not found:
        print("No tokens yet. Create one with `ticorom token add <name>`.")
        return
    when = lambda stamp: time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp)) if stamp else "never"  # noqa: E731
    print(f"{'NAME':<24} {'STATE':<8} {'CREATED':<17} {'LAST USED':<17} CONSOLES")
    for token in found:
        state = "revoked" if token.revoked_at else "active"
        consoles = ", ".join(token.platforms) if token.platforms else "all"
        print(f"{token.name:<24} {state:<8} {when(token.created_at):<17} {when(token.last_used_at):<17} {consoles}")

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

    token = commands.add_parser("token", help="create, list or revoke the access tokens tico uses")

    token_commands = token.add_subparsers(dest="token_command", required=True)

    add = token_commands.add_parser("add", help="create a token and show the addresses to enter in tico")
    add.add_argument("name", help='a label for the token, e.g. "switch"')
    add.add_argument("--platforms", metavar="CONSOLES",
                     help="comma-separated tico console names this token may see, e.g. gba,snes (default: all)")

    token_commands.add_parser("list", help="show every token and when it was last used")

    revoke = token_commands.add_parser("revoke", help="stop a token from working")
    revoke.add_argument("name")

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

    # Token commands only need the settings, the map and the database, not RomM
    if args.command == "token":
        _token_command(args, settings, platform_map, _database_or_exit(settings))
        return

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
        create_app(settings, platform_map, database, syncer), host=args.host, port=args.port, log_level="info"
    )
