from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ticorom.startup.platforms import PlatformMap, Status
from ticorom.startup.settings import Settings
from ticorom.romm import RomMClient, RomMError, RomMPlatform, is_tested_version

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class Report:
    checks: list[tuple[str, str]] = field(default_factory=list)

    # Add a message to the report
    def add(self, level: str, message: str) -> None:
        self.checks.append((level, message))

    # Check if the report passses
    @property
    def passed(self) -> bool:
        return all(level != FAIL for level, _ in self.checks)

    # Returns the report as a list
    def lines(self) -> list[str]:
        return [f"  {level:<4}  {message}" for level, message in self.checks]


class PathDoesNotExistError(Exception):
    """ Path does not exist or is not a directory """

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class PathIsEmptyError(Exception):
    """ Path is empty, may indicate read issues """

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class PathIsNotReadableError(Exception):
    """ Path cannot be read from """

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class PathIsNotWritableError(Exception):
    """ Path cannot be written to """

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


# Check if a folder exists and is readable
def _check_readable_folder(path: Path) -> None:
    if not path.is_dir():
        raise PathDoesNotExistError(f"Directory {path} does not exist.")
    try:
        entries = sum(1 for _ in path.iterdir())
    except OSError as error:
        raise PathIsNotReadableError(f"Directory {path} cannot be read: {error.strerror}.")
    if entries == 0:
        raise PathIsEmptyError(f"Directory {path} is empty.")


# Check if a folder writable
def _check_writable_folder(path: Path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".ticorom-write-test-"):
            pass
    except OSError as error:
        raise PathIsNotWritableError(f"Directory {path} cannot be written: {error.strerror}.")


def _check_platform_coverage(report: Report, platform_map: PlatformMap, platforms: list[RomMPlatform]) -> list[
    RomMPlatform]:
    # Sort RomMPlatforms by status in the platform map
    by_status: dict[Status, list[RomMPlatform]] = {status: [] for status in Status}
    for platform in platforms:
        by_status[platform_map.status(platform.fs_slug, platform.slug)].append(platform)

    # At least one platform must be published
    published = by_status[Status.PUBLISHED]
    names = lambda group: ", ".join(sorted(p.fs_slug for p in group))  # noqa: E731
    if published:
        games = sum(p.rom_count for p in published)
        report.add(OK, f"{len(published)} platforms will be published ({games} games): {names(published)}")
    else:
        report.add(FAIL, "No RomM platform matches an entry in platforms.yaml, so nothing would be published.")

    # Platforms may be explicitly excluded to remove the warnings about not having an entry
    if by_status[Status.EXCLUDED] or by_status[Status.DISABLED]:
        left_out = by_status[Status.EXCLUDED] + by_status[Status.DISABLED]
        report.add(OK, f"{len(left_out)} platforms are excluded or disabled in platforms.yaml: {names(left_out)}")
    if by_status[Status.UNMAPPED]:
        report.add(WARN,
                   f"RomM platforms with no entry in platforms.yaml (not published): {names(by_status[Status.UNMAPPED])}. Add them under 'platforms' or 'exclude'.")

    # Unknown platforms are not a problem, but should be removed
    known = {p.fs_slug for p in platforms} | {p.slug for p in platforms}
    unused = sorted(key for key in platform_map.platforms if key not in known)
    if unused:
        report.add(WARN, f"platforms.yaml entries with no matching RomM platform (ignored): {', '.join(unused)}")

    return published


async def _check_path_mapping(report: Report, romm: RomMClient, library: Path, published: list[RomMPlatform]) -> None:
    """Take one game from RomM and make sure its file is where LIBRARY_PATH says."""

    # Loop through published platforms
    for platform in published:

        # Skip Platforms with no roms
        if platform.rom_count == 0:
            continue

        # Get a single 'page' of roms and check for files
        # Also checks if we have read access for games
        page = await romm.roms_page(platform.id, limit=1)
        files = page.items[0].files if page.items else []
        if not files:
            continue

        # Get the absolute path of the first file
        path = library / files[0].full_path

        # Check that we have read access to the file and that it actually exists
        if path.is_file() and os.access(path, os.R_OK):
            report.add(OK, f"RomM's paths match the library folder (checked {files[0].full_path})")
        elif path.exists():
            report.add(FAIL, f"{path} exists but cannot be read. Check PUID and PGID.")
        else:
            report.add(FAIL,
                       f"RomM lists {files[0].full_path}, but {path} does not exist. LIBRARY_PATH must be the folder RomM uses as its library (the one that contains 'roms').")
        return

    # No games exist in library, cannot publish any games
    report.add(FAIL, "No published platform has a game yet, so the library path could not be checked against RomM.")


async def run_preflight(settings: Settings, platform_map: PlatformMap,
                        transport: httpx.AsyncBaseTransport | None = None) -> Report:
    report = Report()

    # Public address check, settings.py already checks if this is valid
    report.add(OK, f"Public address is {settings.app_url}")

    # Library check
    try:
        _check_readable_folder(settings.library_path)
    except PathDoesNotExistError as error:
        report.add(FAIL, f"{error.message} Check LIBRARY_PATH and the volume mount.")
    except PathIsNotReadableError as error:
        report.add(FAIL, f"{error.message} Check PUID and PGID.")
    except PathIsEmptyError as error:
        report.add(FAIL, f"{error.message} Is the library mounted?")
    else:
        report.add(OK, f"Library folder {settings.library_path} exists and is readable")

    # Config Check
    try:
        _check_writable_folder(settings.config_path)
    except PathIsNotWritableError as error:
        report.add(FAIL, f"{error.message} Check PUID and PGID.")
    else:
        report.add(OK, f"Config folder {settings.config_path} is writable")

    # Cache Check
    try:
        _check_writable_folder(settings.cache_path)
    except PathIsNotWritableError as error:
        report.add(FAIL, f"{error.message} Check PUID and PGID.")
    else:
        report.add(OK, f"Cache folder {settings.cache_path} is writable")

    # Connect with RomM and verify permissions
    try:
        async with RomMClient(settings.romm_url, settings.romm_api_token.get_secret_value(), transport) as romm:
            version = await romm.version()

            # Check if this a version of RomM that has been tested
            if is_tested_version(version):
                report.add(OK, f"RomM {version} at {settings.romm_url}")
            else:
                report.add(WARN,
                           f"RomM {version} at {settings.romm_url} is not a version TicoRom was tested with (5.x)")

            # Verify API Token has platform permissions
            platforms = await romm.platforms()
            report.add(OK, f"The API token works ({len(platforms)} platforms in RomM)")

            # Check if platforms are covered and that at least one is published
            published = _check_platform_coverage(report, platform_map, platforms)

            # Check that we have read access to roms and that their files actually exist where they should
            if settings.library_path.is_dir():
                await _check_path_mapping(report, romm, settings.library_path, published)
    except RomMError as error:
        report.add(FAIL, str(error))

    return report
