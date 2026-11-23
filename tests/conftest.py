import pytest

from ticorom.startup.settings import Settings

SETTING_NAMES = ["APP_URL", "ROMM_URL", "ROMM_API_TOKEN", "INSTANCE_SLUG", "SYNC_INTERVAL_MINUTES",
                 "LIBRARY_PATH", "CONFIG_PATH", "CACHE_PATH", "CACHE_MAX_GB", "SKIP_FILES_OVER_4GIB",
                 "HASH_WORKERS", "HASH_MAX_MIB_PER_SECOND"]


@pytest.fixture(autouse=True)
def no_ambient_settings(monkeypatch, tmp_path):
    """Tests never see the developer's real environment or .env file."""
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def make_settings(tmp_path):
    def build(**overrides) -> Settings:
        values = {
            "app_url": "https://tico.example",
            "romm_url": "https://romm.example",
            "romm_api_token": "rmm_test",
            "library_path": tmp_path / "library",
            "config_path": tmp_path / "config",
            "cache_path": tmp_path / "cache",
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return build
