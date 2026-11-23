import pytest
from pydantic import ValidationError

from ticorom.startup.settings import Settings, describe_errors


def test_urls_lose_their_trailing_slash(make_settings):
    settings = make_settings(app_url="https://tico.example/", romm_url="http://romm:8080/")
    assert settings.app_url == "https://tico.example"
    assert settings.romm_url == "http://romm:8080"


def test_app_url_must_be_https(make_settings):
    with pytest.raises(ValidationError) as caught:
        make_settings(app_url="http://tico.example")
    assert describe_errors(caught.value) == ["APP_URL must start with https:// (tico only connects over HTTPS)"]


@pytest.mark.parametrize("slug", ["Home", "my slug", "-home", "", "x" * 33])
def test_instance_slug_must_fit_a_package_id(make_settings, slug):
    with pytest.raises(ValidationError):
        make_settings(instance_slug=slug)


@pytest.mark.parametrize("slug", ["home", "h", "my-romm.2", "a_b"])
def test_valid_instance_slugs(make_settings, slug):
    assert make_settings(instance_slug=slug).instance_slug == slug


def test_missing_settings_are_named_without_values():
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, app_url="https://tico.example")
    assert describe_errors(caught.value) == ["ROMM_URL is not set", "ROMM_API_TOKEN is not set"]


def test_token_is_not_shown_when_settings_are_printed(make_settings):
    settings = make_settings(romm_api_token="rmm_secret_value")
    assert "rmm_secret_value" not in repr(settings)
    assert settings.romm_api_token.get_secret_value() == "rmm_secret_value"


def test_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://tico.example")
    monkeypatch.setenv("ROMM_URL", "https://romm.example")
    monkeypatch.setenv("ROMM_API_TOKEN", "rmm_x")
    monkeypatch.setenv("SKIP_FILES_OVER_4GIB", "false")
    monkeypatch.setenv("PUID", "1000")  # used by the container entrypoint, ignored here
    settings = Settings(_env_file=None)
    assert settings.skip_files_over_4gib is False
    assert settings.instance_slug == "home"


def test_hashing_settings(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://tico.example")
    monkeypatch.setenv("ROMM_URL", "https://romm.example")
    monkeypatch.setenv("ROMM_API_TOKEN", "rmm_x")
    defaults = Settings(_env_file=None)
    assert (defaults.hash_workers, defaults.hash_max_mib_per_second) == (1, 0)
    monkeypatch.setenv("HASH_WORKERS", "3")
    monkeypatch.setenv("HASH_MAX_MIB_PER_SECOND", "20")
    settings = Settings(_env_file=None)
    assert (settings.hash_workers, settings.hash_max_mib_per_second) == (3, 20)


@pytest.mark.parametrize("name, value", [("HASH_WORKERS", "0"), ("HASH_MAX_MIB_PER_SECOND", "-1")])
def test_bad_hashing_settings_are_refused(monkeypatch, name, value):
    monkeypatch.setenv("APP_URL", "https://tico.example")
    monkeypatch.setenv("ROMM_URL", "https://romm.example")
    monkeypatch.setenv("ROMM_API_TOKEN", "rmm_x")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
