import asyncio

import httpx
import pytest

from ticorom.startup.preflight import FAIL, WARN, run_preflight
from ticorom.startup.platforms import load_platform_map, MAP_FILE_NAME

PLATFORMS = [
    {"id": 1, "slug": "gba", "fs_slug": "gba", "name": "Game Boy Advance", "rom_count": 1},
    {"id": 2, "slug": "ps3", "fs_slug": "ps3", "name": "PlayStation 3", "rom_count": 5},
    {"id": 3, "slug": "saturn", "fs_slug": "saturn", "name": "Saturn", "rom_count": 2},
]
ROM = {
    "id": 10, "platform_id": 1, "fs_name": "Game.7z", "full_path": "roms/gba/Game.7z",
    "has_simple_single_file": True,
    "files": [{"id": 100, "file_name": "Game.7z", "full_path": "roms/gba/Game.7z", "file_size_bytes": 4}],
}


def fake_romm(version="5.4.1", platforms=PLATFORMS, roms=(ROM,), status=200):
    """A stand-in for RomM's API. Records the requests it receives."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/heartbeat":
            return httpx.Response(200, json={"SYSTEM": {"VERSION": version}})
        if status != 200:
            return httpx.Response(status, json={"detail": "nope"})
        if request.url.path == "/api/platforms":
            return httpx.Response(200, json=platforms)
        if request.url.path == "/api/roms":
            return httpx.Response(200, json={"items": list(roms), "total": len(roms)})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    transport.seen = seen
    return transport


@pytest.fixture
def settings(make_settings, tmp_path):
    game = tmp_path / "library" / "roms" / "gba" / "Game.7z"
    game.parent.mkdir(parents=True)
    game.write_bytes(b"data")
    return make_settings()


def run(settings, transport):
    platform_map, created = load_platform_map(settings.config_path)
    return asyncio.run(run_preflight(settings, platform_map, transport))


def messages(report, level):
    return [message for found, message in report.checks if found == level]


def test_a_working_setup_passes(settings):
    transport = fake_romm()
    report = run(settings, transport)
    assert report.passed, report.lines()
    assert (settings.config_path / MAP_FILE_NAME).exists()
    assert any("roms/gba/Game.7z" in line for line in report.lines())
    # The token travels as a bearer header and never appears in the report.
    assert transport.seen[-1].headers["Authorization"] == "Bearer rmm_test"
    assert "rmm_test" not in "\n".join(report.lines())


def test_unmapped_platforms_are_a_warning_and_excluded_ones_are_not(settings):
    report = run(settings, fake_romm())
    assert "saturn" in messages(report, WARN)[0] and "ps3" not in messages(report, WARN)[0]


def test_excluded_platforms_are_never_requested(settings):
    transport = fake_romm()
    run(settings, transport)
    requested = [r.url.params["platform_ids"] for r in transport.seen if r.url.path == "/api/roms"]
    assert requested == ["1"]


def test_wrong_library_folder_fails_with_advice(settings, make_settings, tmp_path):
    (tmp_path / "elsewhere" / "stuff").mkdir(parents=True)
    report = run(make_settings(library_path=tmp_path / "elsewhere"), fake_romm())
    assert not report.passed
    assert "LIBRARY_PATH must be the folder RomM uses" in messages(report, FAIL)[0]


def test_missing_library_folder_fails(make_settings, tmp_path):
    report = run(make_settings(library_path=tmp_path / "nowhere"), fake_romm())
    assert any("does not exist" in message for message in messages(report, FAIL))


@pytest.mark.parametrize("status, expected", [(401, "rejected the API token"), (403, "roms.read and platforms.read")])
def test_token_problems_are_explained(settings, status, expected):
    report = run(settings, fake_romm(status=status))
    assert not report.passed and expected in messages(report, FAIL)[0]


def test_untested_romm_version_is_a_warning(settings):
    report = run(settings, fake_romm(version="6.0.0"))
    assert report.passed and any("6.0.0" in message for message in messages(report, WARN))


def test_unexpected_response_shape_fails_loudly(settings):
    report = run(settings, fake_romm(platforms=[{"identifier": 1}]))
    assert not report.passed and "does not have the fields TicoRom expects" in messages(report, FAIL)[0]


def test_unreachable_romm_fails(settings):
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    report = run(settings, httpx.MockTransport(refuse))
    assert not report.passed and "cannot reach RomM" in messages(report, FAIL)[0]
