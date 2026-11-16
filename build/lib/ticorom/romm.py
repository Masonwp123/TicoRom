from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

USER_AGENT = "ticorom"
TESTED_MAJOR_VERSIONS = (5,)

class RomMError(Exception):
    """RomM could not be reached or refused the request. The message is ready to show."""

class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")

# Represents a RomM platform
class RomMPlatform(_Model):
    id: int
    slug: str
    fs_slug: str
    name: str = ""
    rom_count: int = 0

# Represents a single file that RomM delivers
class RomMFile(_Model):
    id: int
    file_name: str
    full_path: str
    file_size_bytes: int = 0
    is_top_level: bool = True
    category: str | None = None

# Represents a single rom with all the info we need to represent it to tico
class RomMRom(_Model):
    id: int
    platform_id: int
    platform_fs_slug: str = ""
    name: str | None = None
    fs_name: str
    full_path: str
    summary: str | None = None
    path_cover_small: str | None = None
    has_simple_single_file: bool = False
    has_nested_single_file: bool = False
    has_multiple_files: bool = False
    files: list[RomMFile] = []

# Represents a page of roms with a max size
# Requesting pages prevents slowdowns with large libraries
class RomMPage(_Model):
    items: list[RomMRom]
    total: int | None = None

class RomMClient:
    def __init__(self, base_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:

        # Connect to RomM with our auth token
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            timeout=httpx.Timeout(60, connect=10),
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> RomMClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _get(self, path: str, params: dict | None = None):
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as error:
            raise RomMError(f"cannot reach RomM at {self._client.base_url}: {error or type(error).__name__}") from error
        if response.status_code == 401: # Unauthorized
            raise RomMError("RomM rejected the API token (401). Check ROMM_API_TOKEN.")
        if response.status_code == 403: # Forbidden (No Perms)
            raise RomMError(f"the API token is not allowed to read {path} (403). It needs the scopes roms.read and platforms.read.")
        if response.status_code != 200:
            raise RomMError(f"RomM answered {response.status_code} for {path}")
        try:
            return response.json()
        except ValueError as error:
            raise RomMError(f"RomM did not answer {path} with JSON. Is ROMM_URL the address of RomM itself?") from error

    # Check the health and version of RomM
    async def version(self) -> str:
        data = await self._get("/api/heartbeat")
        version = (data.get("SYSTEM") or {}).get("VERSION") if isinstance(data, dict) else None
        if not version:
            raise RomMError("the RomM heartbeat did not report a version. Is ROMM_URL the address of RomM itself?")
        return str(version)

    # List RomM platforms
    async def platforms(self) -> list[RomMPlatform]:
        data = await self._get("/api/platforms")
        return _parse("/api/platforms", lambda: [RomMPlatform.model_validate(item) for item in data])

    # Get a Page of Roms from a platform
    async def roms_page(self, platform_id: int, limit: int = 100, offset: int = 0) -> RomMPage:
        data = await self._get("/api/roms", {
            "platform_ids": platform_id,
            "with_files": "true",
            "limit": limit,
            "offset": offset,
            "with_char_index": "false",
            "with_filter_values": "false",
            "with_rom_id_index": "false",
        })
        return _parse("/api/roms", lambda: RomMPage.model_validate(data))

# Validate the structure of our models
def _parse(path: str, build):
    try:
        return build()
    except (ValidationError, TypeError) as error:
        first = str(error).splitlines()[:3]
        raise RomMError(f"RomM's answer for {path} does not have the fields TicoRom expects "
                        f"(a RomM version it was not written for?): {' '.join(first)}") from error

# Is the RomM version one that has been tested
def is_tested_version(version: str) -> bool:
    major = version.split(".", 1)[0]
    return major.isdigit() and int(major) in TESTED_MAJOR_VERSIONS
