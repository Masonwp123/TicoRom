from __future__ import annotations

import re
from pathlib import Path
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# A package id is "romm-<slug>-<rom id>", and tico caps ids at 64 characters.
INSTANCE_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")

class Settings(BaseSettings):
    app_url: str
    romm_url: str
    romm_api_token: SecretStr
    instance_slug: str = "home"
    sync_interval_minutes: int = Field(default=60, ge=1)
    library_path: Path = Path("/data/library")
    config_path: Path = Path("/data/config")
    cache_path: Path = Path("/data/cache")
    cache_max_gb: float = Field(default=50, gt=0)
    skip_files_over_4gib: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("app_url")
    @classmethod
    def app_url_is_https(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith("https://"):
            raise ValueError("must start with https:// (tico only connects over HTTPS)")
        return value

    @field_validator("romm_url")
    @classmethod
    def romm_url_is_http(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return value

    @field_validator("instance_slug")
    @classmethod
    def instance_slug_is_id_safe(cls, value: str) -> str:
        if not INSTANCE_SLUG_RE.fullmatch(value):
            raise ValueError("must be 1-32 characters: lowercase letters, digits, '.', '_' or '-', "
                             "starting with a letter or digit")
        return value

def load_settings() -> Settings:
    return Settings()

# Describe the errors in a human-readable lines
def describe_errors(error: ValidationError) -> list[str]:
    lines = []
    for item in error.errors():
        name = str(item["loc"][0]).upper() if item["loc"] else "settings"
        message = "is not set" if item["type"] == "missing" else item["msg"].removeprefix("Value error, ")
        lines.append(f"{name} {message}")
    return lines
