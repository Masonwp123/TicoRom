import gzip
import json
import logging

import pytest
from fastapi.testclient import TestClient

from ticorom import index, tokens
from ticorom.app import HideTokens, create_app
from ticorom.db import Database, connect
from ticorom.startup.platforms import parse_platform_map

MAP = parse_platform_map("platforms: {gba: {tico_slug: gba, extensions: [7z]}}")


@pytest.fixture
def app(make_settings):
    settings = make_settings()
    settings.config_path.mkdir()
    database = Database.in_config(settings.config_path)
    database.migrate()
    folder = settings.cache_path / index.INDEX_DIR
    folder.mkdir(parents=True)
    package = {"id": "romm-home-7", "name": "Quest",
               "url": f"https://tico.example/r/{index.TOKEN_PLACEHOLDER}/dl/romm-home-7/aaaaaaaa/Quest.7z"}
    for name in ("_all", "gba"):
        (folder / f"{name}.json").write_text(json.dumps({"schema": 1, "name": "TicoRom (home)", "packages": [package]}))
    with connect(database.path) as conn:
        everything = tokens.add(conn, "switch")
        limited = tokens.add(conn, "kids", ["snes"], known_platforms={"gba", "snes"})
    with TestClient(create_app(settings, MAP, database)) as client:
        yield client, everything, limited


def test_health(app):
    client, _, _ = app
    assert client.get("/v1/health").json()["status"] == "ok"


def test_the_index_carries_the_requesting_token(app):
    client, token, _ = app
    response = client.get(f"/r/{token}/index.json")
    assert response.status_code == 200 and response.headers["content-type"] == "application/json"
    assert response.json()["packages"][0]["url"] == f"https://tico.example/r/{token}/dl/romm-home-7/aaaaaaaa/Quest.7z"


def test_gzipped_and_per_console_indexes(app):
    client, token, _ = app
    packed = client.get(f"/r/{token}/index.json.gz")
    assert packed.headers["content-type"] == "application/gzip"
    assert json.loads(gzip.decompress(packed.content))["packages"][0]["id"] == "romm-home-7"
    assert client.get(f"/r/{token}/gba/index.json").status_code == 200
    assert client.get(f"/r/{token}/gba/index.json.gz").status_code == 200
    assert client.get(f"/r/{token}/n64/index.json").status_code == 404


@pytest.mark.parametrize("token", ["0" * 32, "not-a-token", ""])
def test_unknown_tokens_get_404(app, token):
    client, _, _ = app
    assert client.get(f"/r/{token}/index.json").status_code == 404


def test_a_limited_token_sees_only_its_consoles(app):
    client, _, limited = app
    assert client.get(f"/r/{limited}/index.json").json()["packages"] == []   # snes has no games yet
    assert client.get(f"/r/{limited}/gba/index.json").status_code == 404


def test_access_log_lines_hide_the_token():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("10.0.0.2:5000", "GET", "/r/" + "a" * 32 + "/gba/index.json", "1.1", 200), None)
    HideTokens().filter(record)
    assert record.getMessage() == '10.0.0.2:5000 - "GET /r/<token>/gba/index.json HTTP/1.1" 200'
