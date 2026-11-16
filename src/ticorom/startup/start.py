from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import ValidationError

import uvicorn

from ticorom import __version__
from ticorom.app import create_app
from ticorom.startup.preflight import run_preflight
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

# Create commands and arguments used for said commands
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ticorom")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("check", help="run the startup checks and exit")

    serve = commands.add_parser("serve", help="run the startup checks, then start the bridge")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)

    args = parser.parse_args()

    return args

def main() -> None:
    args = _parse_args()

    # Get the settings for the app and run the preflight checks
    settings = _settings_or_exit()
    platform_map = _platform_map_or_defaults(settings)
    _preflight_or_exit(settings, platform_map)

    # Check doesn't run the bridge
    if args.command == "check":
        return

    print(f"Starting TicoRom {__version__}:")

    uvicorn.run(
        create_app(settings, platform_map), host=args.host, port=args.port, log_level="info"
    )
