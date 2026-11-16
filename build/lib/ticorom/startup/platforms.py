from __future__ import annotations

import re
from enum import Enum
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MAP_FILE_NAME = "platforms.yaml"
TICO_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

# Options
ZipPolicy = Literal["keep", "extract"]
MultiFile = Literal["bundle", "skip"]
ChdType = Literal["cd", "gd", "dvd"]

class PlatformMapError(Exception):
    """ Base for other errors """

class PlatformMapYamlError(PlatformMapError):
    """ Invalid YAML """
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

class PlatformMapNonDictError(PlatformMapError):
    """ YAML did not return a dictionary """
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

class PlatformMapInvalidError(PlatformMapError):
    """ Invalid platform mapping """
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

class Status(str, Enum):
    PUBLISHED = "published"
    DISABLED = "disabled"
    EXCLUDED = "excluded"
    UNMAPPED = "unmapped"

# Define default policies
class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zip_policy: ZipPolicy = "keep"
    multi_file: MultiFile = "bundle"

# Defines the configuration in a single platform
class Platform(BaseModel):
    tico_slug: str
    install: str = ""
    extensions: list[str] = Field(min_length=1)
    companion_extensions: list[str] = []
    zip_policy: ZipPolicy | None = None
    multi_file: MultiFile | None = None
    chd_types: list[ChdType] | None = None
    enabled: bool = True

    model_config = ConfigDict(extra="forbid")

    @field_validator("extensions", "companion_extensions")
    @classmethod
    def extensions_are_lowercase_without_dot(cls, values: list[str]) -> list[str]:
        return [value.lower().lstrip(".") for value in values]

    @field_validator("tico_slug")
    @classmethod
    def tico_slug_is_a_folder_name(cls, value: str) -> str:
        if not TICO_SLUG_RE.fullmatch(value):
            raise ValueError("use lowercase letters, digits and '-'")
        return value

    @model_validator(mode="after")
    def default_install_folder(self) -> Platform:
        self.install = self.install.strip("/") or f"tico/roms/{self.tico_slug}"
        return self

# Defines the configuration for the overall platform map
class PlatformMap(BaseModel):
    defaults: Defaults = Defaults()
    exclude: list[str] = []
    platforms: dict[str, Platform] = {}

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def excluded_platforms_have_no_entry(self) -> PlatformMap:
        both = sorted(set(self.exclude) & set(self.platforms))
        if both:
            raise ValueError(f"listed under both 'platforms' and 'exclude': {', '.join(both)}")
        return self

    # Get the status of a platform
    def status(self, fs_slug: str, slug: str | None = None) -> Status:
        for key in (fs_slug, slug):
            if key in self.platforms:
                return Status.PUBLISHED if self.platforms[key].enabled else Status.DISABLED
        if fs_slug in self.exclude or slug in self.exclude:
            return Status.EXCLUDED
        return Status.UNMAPPED

    def entry(self, fs_slug: str, slug: str | None = None) -> Platform | None:
        return self.platforms.get(fs_slug) or self.platforms.get(slug or "")

# Parse the platform mapping yaml
def parse_platform_map(text: str, source: str = MAP_FILE_NAME) -> PlatformMap:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        raise PlatformMapYamlError(f"{source} is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise PlatformMapNonDictError(f"{source} must be a mapping with 'platforms' and 'exclude'")
    try:
        return PlatformMap.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'file'}: {item['msg'].removeprefix('Value error, ')}"
            for item in error.errors()
        )
        raise PlatformMapInvalidError(f"{source} is invalid: {problems}") from error

# Get default platform mapping path
def default_map_path() -> Traversable:
    return resources.files("ticorom.startup").joinpath("default_platforms.yaml")

# Get default platform mapping text
def default_map_text() -> str:
    return default_map_path().read_text(encoding="utf-8")

# Attempt to load the platform map from config_path, otherwise load defaults and loudly warn
def load_platform_map(config_path: Path) -> tuple[PlatformMap, bool]:
    path = config_path / MAP_FILE_NAME
    created = False

    if not path.exists():
        try:
            path.write_text(default_map_text(), encoding="utf-8")
        except OSError as error:
            print(f"  WARN  Could not find or write default platforms to config: {error.strerror}.")
        else:
            created = True

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"  WARN  Could not read platform map, using defaults: {error.strerror}.")
        path = default_map_path()
        text = default_map_text()

    return parse_platform_map(text, str(path)), created
